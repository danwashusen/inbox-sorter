"""Run Cloudflare clef in process on the local GPU (MPS) or CPU."""

import logging
import sys
from contextlib import nullcontext

from inbox_sorter.engine import DecisionRequest, DecisionResponse
from inbox_sorter.engines.systemone import from_response_body, to_request_body


TORCH_REDIRECTS_LOGGER = "torch.distributed.elastic.multiprocessing.redirects"
# Warns on the first decision that it uses slower reference kernels.
KERNELS_LOGGER = "transformers.integrations.hub_kernels"


def _errors_only(record: logging.LogRecord) -> bool:
    return record.levelno >= logging.ERROR


class ClefLocalEngine:
    runs_locally = True

    def __init__(self, model_id: str = "Cloudflare/clef", device: str | None = None) -> None:
        # Heavy imports stay inside the adapter so the core never needs torch.
        import torch
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import LocalEntryNotFoundError
        from huggingface_hub.utils import disable_progress_bars
        from transformers.utils import logging as transformers_logging

        # Quiet the load: no progress bars once the weights are on disk, and
        # no slower-kernel warnings or torch's macOS note. Other warnings,
        # such as weights missing from the checkpoint, still show, and a
        # first download still shows its progress.
        for name in (KERNELS_LOGGER, TORCH_REDIRECTS_LOGGER):
            logging.getLogger(name).addFilter(_errors_only)
        try:
            snapshot_download(model_id, local_files_only=True)
            bars = disable_progress_bars()
        except LocalEntryNotFoundError:
            bars = nullcontext()
        with bars:
            path = snapshot_download(model_id)  # still fetches a newer revision
        # After the download: this also turns off every Hugging Face Hub bar.
        transformers_logging.disable_progress_bar()
        # The model's loader ships as a module inside the model repo.
        if path not in sys.path:
            sys.path.insert(0, path)
        from joint_schema_model import load_release_model, systemone

        self.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        self.name = f"clef-local:{model_id}"
        self._systemone = systemone
        self._model, self._processor = load_release_model(
            path, device=self.device, dtype=torch.bfloat16
        )

    def decide(self, request: DecisionRequest) -> DecisionResponse:
        body = to_request_body(request, model="clef")
        response = self._systemone(self._model, self._processor, body)
        return from_response_body(request, response, self.name)

    def close(self) -> None:
        self._model = self._processor = None
