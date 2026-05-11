# from agent.normal_agent import NormalToolAgent
from agent.graph_agent import GraphAgent
from utils.logger import logger

if __name__ == "__main__":
    # 二选一切换
    # agent = NormalToolAgent()
    agent = GraphAgent()

    logger.info("LangChain Agent 脚手架启动成功，输入 exit 退出，输入 clear 可清空上下文")

    while True:
        user_input = input("请输入问题：")
        if user_input.lower() in ["exit", "quit"]:
            logger.info("退出对话")
            break
        if user_input.lower() in ["clear", "reset"]:
            agent.reset()
            print("🤖 Agent回答：已清空当前会话上下文。")
            print("-" * 60)
            continue
        answer = agent.run(user_input)
        print("🤖 Agent回答：", answer)
        print("-" * 60)
