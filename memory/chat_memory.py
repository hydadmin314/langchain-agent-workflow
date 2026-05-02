from langchain.memory import ConversationBufferMemory

def get_chat_memory() -> ConversationBufferMemory:
    return ConversationBufferMemory(
        memory_key="chat_history",
        return_messages=True
    )