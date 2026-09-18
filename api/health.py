import json


def handler(request):
    body=json.dumps({
        "ok": True,
        "version": "5.0",
        "service": "Cime dos Mundos",
        "deployment": "vercel",
        "online": True,
        "public_url": "https://cime-dos-mundos.vercel.app"
    }, ensure_ascii=False).encode("utf-8")
    return Response(body, status=200, headers={
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        "Content-Length": str(len(body)),
    })
