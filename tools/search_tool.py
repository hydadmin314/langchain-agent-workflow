from langchain.tools import tool


@tool
def search_internet(query: str) -> str:
    """
    用于查询实时信息、新闻、天气、百科等外部知识。

    Args:
        query: 用户要检索的问题
    """
    return f"【模拟搜索】关于“{query}”的相关信息已查询完成。"
