from langchain.tools import tool

@tool
def calculator(expression: str) -> str:
    """
    用于数学计算
    Args:
        expression: 数学表达式，例如 12*5+3
    """
    try:
        res = eval(expression)
        return f"计算结果：{res}"
    except Exception as e:
        return f"计算失败：{str(e)}"