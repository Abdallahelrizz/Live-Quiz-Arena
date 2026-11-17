"""Fat-Rat clients.

core             - ClientSession: all client networking, no interface code
storage          - saved host identity and reconnect tokens (a local JSON file)
quizfile         - load and save quiz files
terminal_host    - host in a terminal:    python3 -m fatrat.client.terminal_host --server <IP>
terminal_player  - player in a terminal:  python3 -m fatrat.client.terminal_player --server <IP>
bot              - simulated hosts and players for tests and the demo
"""
