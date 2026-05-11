from langchain.tools import tool


@tool
def calculator(expression: str) -> str:
    """
    用于执行简单数学计算。

    Args:
        expression: 数学表达式，例如 12*5+3
    """
    try:
        result = eval(expression)
        return f"计算结果：{result}"
    except Exception as exc:
        return f"计算失败：{exc}"
