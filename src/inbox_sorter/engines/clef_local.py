"""Run Cloudflare clef in process on the local GPU (MPS) or CPU."""

import sys

from inbox_sorter.engine import DecisionRequest, DecisionResponse
from inbox_sorter.engines.systemone import from_response_body, to_request_body


class ClefLocalEngine:
    runs_locally = True

    def __init__(self, model_id: str = "Cloudflare/clef", device: str | None = None) -> None:
        # Heavy imports stay inside the adapter so the core never needs torch.
        import torch
        from huggingface_hub import snapshot_download

        path = snapshot_download(model_id)
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
