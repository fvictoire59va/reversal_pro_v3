"""Query recent positions from the production DB via SSH + psql in timescaledb container."""
import paramiko
import sys

def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect('176.131.66.167', username='fred', password='victoire')

    # Get the timescaledb container ID
    stdin, stdout, stderr = c.exec_command("docker ps --format '{{.ID}} {{.Names}}' | grep timescaledb | head -1 | awk '{print $1}'")
    container_id = stdout.read().decode().strip()
    if not container_id:
        print("ERROR: TimescaleDB container not found")
        c.close()
        return

    print(f"Container: {container_id}")

    query = sys.argv[1] if len(sys.argv) > 1 else """
        SELECT id, agent_id, symbol, side, entry_price, stop_loss, original_stop_loss,
               take_profit, tp2, status, opened_at, closed_at, exit_price,
               pnl, pnl_percent, best_price, partial_closed, quantity,
               entry_signal_time
        FROM agent_positions
        WHERE opened_at >= '2026-02-19 00:00:00'
        ORDER BY opened_at DESC
        LIMIT 20;
    """

    cmd = f"docker exec {container_id} psql -U reversal -d reversaldb -c \"{query}\""
    stdin, stdout, stderr = c.exec_command(cmd, get_pty=True)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if out:
        print(out)
    if err:
        print("STDERR:", err)
    c.close()

if __name__ == "__main__":
    main()
