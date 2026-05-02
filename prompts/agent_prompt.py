from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

def get_agent_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages([
        ("system", "你是专业智能Agent，可以使用工具完成计算和信息查询，一步步思考后给出准确答案。"),
        ("user", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad")
    ])