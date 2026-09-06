import os, requests
url=os.environ["DREAMARTS_AGENT_URL"]
token=os.environ["DREAMARTS_AGENT_TOKEN"]
r=requests.post(url,headers={"Authorization":"Bearer "+token},timeout=30)
r.raise_for_status()
print(r.json())
