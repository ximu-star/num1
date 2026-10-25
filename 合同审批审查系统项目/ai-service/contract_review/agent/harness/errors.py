from enum import StrEnum


class CorrectableErrorCode(StrEnum):
    """定义模型重新生成可以修复的输出错误码。"""

    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    UNSUPPORTED_FACT = "UNSUPPORTED_FACT"
    CLAUSE_SCHEMA_INVALID = "CLAUSE_SCHEMA_INVALID"
    COMMENT_EMPTY = "COMMENT_EMPTY"
    COMMENT_TOO_LONG = "COMMENT_TOO_LONG"


class TerminalErrorCode(StrEnum):
    """定义模型重新生成无法修复的执行错误码。"""

    MODEL_CALL_FAILED = "MODEL_CALL_FAILED"
    OUTPUT_VALIDATION_FAILED = "OUTPUT_VALIDATION_FAILED"
    AGENT_EXECUTION_FAILED = "AGENT_EXECUTION_FAILED"
    TASK_NOT_READY = "TASK_NOT_READY"
    WRITEBACK_FAILED = "WRITEBACK_FAILED"


type AgentErrorCode = CorrectableErrorCode | TerminalErrorCode


class AgentOutputValidationError(ValueError):
    """表示 Agent 输出违反可确定校验的业务规则。"""

    def __init__(self, code: CorrectableErrorCode, message: str):
        super().__init__(message)
        self.code = code


class AgentExecutionError(RuntimeError):
    """表示本次 Agent Run 无法继续执行。"""

    def __init__(self, code: AgentErrorCode, message: str):
        super().__init__(message)
        self.code = code
