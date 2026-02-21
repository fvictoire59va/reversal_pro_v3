"""Temporary helper to run remote commands via SSH."""
import paramiko
import sys

def run_remote(cmd):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect('176.131.66.167', username='fred', password='victoire')
    stdin, stdout, stderr = c.exec_command(cmd, get_pty=True)
    out = stdout.read().decode()
    err = stderr.read().decode()
    c.close()
    if out:
        print(out)
    if err:
        print("STDERR:", err)

if __name__ == "__main__":
    cmd = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "docker ps"
    run_remote(cmd)
