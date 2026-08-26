"""Exemplo mínimo de uso do SDK Venus."""

from venus.agents.base_agent import BaseAgent


class EchoAgent(BaseAgent):
    name = "echo_agent"

    def run(self, message: str) -> str:
        return message


if __name__ == "__main__":
    agent = EchoAgent()
    print(agent.run("Olá, Venus!"))
