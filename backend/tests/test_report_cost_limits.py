from app.config import Config
from app.services.report_agent import ReportAgent, ReportOutline, ReportSection


def test_configured_tool_limit_allows_final_answer_after_one_search(monkeypatch):
    monkeypatch.setattr(Config, "REPORT_AGENT_MAX_TOOL_CALLS", 1)

    class LLM:
        def __init__(self):
            self.responses = iter([
                '<tool_call>{"name":"quick_search","parameters":{"query":"test"}}</tool_call>',
                'Final Answer: 基于检索结果的内容',
            ])
            self.calls = 0
            self.system_prompt = None

        def chat(self, **_kwargs):
            self.calls += 1
            self.system_prompt = _kwargs["messages"][0]["content"]
            return next(self.responses)

    llm = LLM()
    agent = ReportAgent("graph", "sim", "小规模测试", llm_client=llm, zep_tools=object())
    executions = []
    monkeypatch.setattr(agent, "_execute_tool", lambda name, params, **kwargs: executions.append(name) or "测试结果")
    section = ReportSection(title="章节", content="")
    outline = ReportOutline(title="报告", summary="测试", sections=[section])

    assert agent._generate_section_react(section, outline, []) == "基于检索结果的内容"
    assert agent.MAX_TOOL_CALLS_PER_SECTION == 1
    assert executions == ["quick_search"]
    assert llm.calls == 2
    assert "至少调用1次工具（最多1次）" in llm.system_prompt
