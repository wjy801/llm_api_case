from __future__ import annotations

from common.base_decorators import BaseDecorators


def sample_poll_generation_result(task_id: str):
    return None


class TestBaseDecorators:
    def test_formats_step_title_from_function_arguments(self):
        decorators = BaseDecorators()

        step_title = decorators._build_step_title(
            sample_poll_generation_result,
            "轮询异步生成结果: {task_id}",
            ("task-001",),
            {},
        )

        assert step_title == "轮询异步生成结果: task-001"
