import urllib.request
import json

url = "http://176.131.66.167:8080/api/v1/optimizer/start?symbol=BTC%2FUSDT"
try:
    req = urllib.request.Request(url, method="POST")
    r = urllib.request.urlopen(req)
    print(f"STATUS: {r.status}")
    print(f"BODY: {r.read().decode()}")
except urllib.error.HTTPError as e:
    print(f"HTTP ERROR: {e.code}")
    print(f"BODY: {e.read().decode()}")
except Exception as e:
    print(f"ERROR: {e}")
