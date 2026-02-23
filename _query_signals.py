"""Query latest signals from remote DB via SSH."""
import paramiko

HOST = "192.168.1.41"
USER = "fred"
PASS = "victoire"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASS, timeout=10)

_, stdout, _ = ssh.exec_command("docker ps --format '{{.Names}}' | grep timescale", timeout=15)
container = stdout.read().decode().strip()
print(f"Container: {container}\n")

def query(label, sql):
    cmd = f'docker exec {container} psql -U reversal -d reversaldb -c "{sql}"'
    _, out, err = ssh.exec_command(cmd, timeout=30)
    print(f"--- {label} ---")
    print(out.read().decode())
    e = err.read().decode()
    if e:
        print("STDERR:", e)

# 1. ALL signals (including preview) from Feb 23
query("All signals 1h (inc. preview) recent",
    "SELECT time, is_bullish, is_preview, price, signal_label "
    "FROM signals WHERE symbol = 'BTC/USDT' AND timeframe = '1h' "
    "ORDER BY time DESC LIMIT 20;")

# 2. Agent config for opti_1h_4
query("Agent opti_1h_4 config",
    "SELECT name, sensitivity, signal_mode, confirmation_bars, "
    "atr_length, average_length, absolute_reversal, is_active "
    "FROM agents WHERE name = 'opti_1h_4';")

# 3. Open position
query("opti_1h_4 positions",
    "SELECT side, entry_price, status, opened_at, entry_signal_id, entry_signal_time, entry_signal_is_bullish "
    "FROM agent_positions WHERE agent_id = "
    "(SELECT id FROM agents WHERE name = 'opti_1h_4') "
    "ORDER BY opened_at DESC LIMIT 5;")

# 4. Agent logs (last 15)
query("opti_1h_4 logs",
    "SELECT action, details->>'side' as side, "
    "details->>'reason' as reason, "
    "details->>'signal_time' as sig_time, "
    "created_at "
    "FROM agent_logs WHERE agent_id = "
    "(SELECT id FROM agents WHERE name = 'opti_1h_4') "
    "ORDER BY created_at DESC LIMIT 15;")

# 5. Check all column names on signals table
query("Signals columns",
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name = 'signals' ORDER BY ordinal_position;")

ssh.close()
