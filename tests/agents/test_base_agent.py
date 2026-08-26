from venus.agents.base_agent import BaseAgent


class EchoAgent(BaseAgent):
    name = "test_agent"

    def run(self, message: str) -> str:
        return message


def test_echo_agent_returns_the_message():
    agent = EchoAgent()
    assert agent.run("ola") == "ola"