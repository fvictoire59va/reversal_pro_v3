"""Run a command on the remote server via SSH."""
import sys
import paramiko

HOST = "176.131.66.167"
USER = "fred"
PASS = "victoire"

cmd = sys.argv[1] if len(sys.argv) > 1 else "echo OK"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASS, timeout=10)
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
out = stdout.read().decode()
err = stderr.read().decode()
code = stdout.channel.recv_exit_status()
if out:
    print(out, end="")
if err:
    print(err, end="", file=sys.stderr)
ssh.close()
sys.exit(code)
