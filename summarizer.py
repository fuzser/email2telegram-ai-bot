"""LLM 摘要客户端占位模块。"""


class Summarizer:
    """使用 OpenAI-compatible API 生成事实性邮件摘要。"""

    def summarize(self, sender: str, subject: str, body: str) -> list[str]:
        """返回最多三条摘要要点。"""
        raise NotImplementedError("LLM summarization will be implemented next.")
