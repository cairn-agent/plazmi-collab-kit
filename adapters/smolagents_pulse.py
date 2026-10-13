"""Optional smolagents tool; no LLM invocation or resident creation at import."""
import json
from smolagents import Tool
from plazmi_status import read_status


class PlazmiPulse(Tool):
    name = 'plazmi_pulse'
    description = 'Read counts for an already configured Plazmi game resident. Does not perform game actions.'
    inputs = {}
    output_type = 'string'

    def __init__(self, cli='plazmi'):
        self.cli = cli
        super().__init__()

    def forward(self):
        code, value = read_status('pulse', self.cli)
        return json.dumps({'cli_exit_code': code, 'result': value})
