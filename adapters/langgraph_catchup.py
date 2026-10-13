"""A bounded, model-free LangGraph: read pulse, then digest only if notable."""
from typing import TypedDict
from langgraph.graph import END, START, StateGraph
from plazmi_status import read_status


class Catchup(TypedDict, total=False):
    cli: str
    exit_code: int
    pulse: dict
    digest: dict


def pulse(state):
    code, result = read_status('pulse', state.get('cli', 'plazmi'))
    return {'exit_code': code, 'pulse': result}


def route(state):
    if state['exit_code'] != 0:
        return END
    return 'digest' if state['pulse'].get('notable', 0) else END


def digest(state):
    code, result = read_status('digest', state.get('cli', 'plazmi'))
    return {'exit_code': code, 'digest': result}


def build_graph():
    builder = StateGraph(Catchup)
    builder.add_node('pulse', pulse)
    builder.add_node('digest', digest)
    builder.add_edge(START, 'pulse')
    builder.add_conditional_edges('pulse', route, ['digest', END])
    builder.add_edge('digest', END)
    return builder.compile()
