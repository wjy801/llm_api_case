from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapturePolicy:
    capture_input_media: bool = True
    capture_output_results: bool = True
    max_input_bytes: int | None = None
    max_output_bytes: int | None = None

    def __post_init__(self) -> None:
        for name in ("max_input_bytes", "max_output_bytes"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be greater than 0")

    @classmethod
    def disabled(cls) -> CapturePolicy:
        return cls(
            capture_input_media=False,
            capture_output_results=False,
        )

    @classmethod
    def input_only(cls, *, max_bytes: int | None = None) -> CapturePolicy:
        return cls(
            capture_input_media=True,
            capture_output_results=False,
            max_input_bytes=max_bytes,
        )

    @classmethod
    def output_only(cls, *, max_bytes: int | None = None) -> CapturePolicy:
        return cls(
            capture_input_media=False,
            capture_output_results=True,
            max_output_bytes=max_bytes,
        )


DEFAULT_CAPTURE_POLICY = CapturePolicy()
