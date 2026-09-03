#!/usr/bin/env python3
"""Local Anthropic Messages -> Microsoft Foundry Responses API bridge.

Uses only the Python standard library.  Credentials are read from the process
environment and are deliberately never written to request logs.
"""
import json, logging, os, ssl, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger("claude_gpt56_proxy")

def env(name, default=""):
    return os.environ.get(name, default).strip()

def responses_url():
    endpoint = env("FOUNDRY_ENDPOINT").rstrip("/")
    if not endpoint:
        raise ValueError("FOUNDRY_ENDPOINT is not set")
    if endpoint.endswith("/responses"):
        return endpoint
    if "/openai/v1" in endpoint:
        return endpoint + "/responses"
    if ".openai.azure.com" in endpoint:
        return endpoint + "/openai/v1/responses"
    if ".services.ai.azure.com" in endpoint:
        # A project endpoint is accepted too.
        if "/api/projects/" in endpoint:
            return endpoint + "/openai/v1/responses"
    raise ValueError("FOUNDRY_ENDPOINT must be an Azure OpenAI resource URL, an OpenAI v1 base URL, or a Foundry project endpoint")

def content_text(value):
    if isinstance(value, str): return value
    if not isinstance(value, list): return json.dumps(value, ensure_ascii=False)
    out = []
    for b in value:
        if isinstance(b, str): out.append(b)
        elif b.get("type") == "text": out.append(b.get("text", ""))
        elif b.get("type") == "tool_result": out.append(content_text(b.get("content", "")))
    return "".join(out)

def to_openai_input(messages):
    items = []
    for msg in messages or []:
        role, content = msg.get("role", "user"), msg.get("content", "")
        blocks = content if isinstance(content, list) else [{"type":"text", "text":content}]
        normal = []
        for block in blocks:
            typ = block.get("type") if isinstance(block, dict) else "text"
            if typ == "text": normal.append({"type":"input_text", "text":block.get("text", "")})
            elif typ == "image":
                source = block.get("source", {})
                if source.get("type") == "base64":
                    normal.append({"type":"input_image", "image_url":"data:%s;base64,%s" % (source.get("media_type", "image/png"), source.get("data", ""))})
            elif typ == "tool_use":
                if normal: items.append({"type":"message", "role":role, "content":normal}); normal=[]
                items.append({"type":"function_call", "call_id":block.get("id"), "name":block.get("name"), "arguments":json.dumps(block.get("input", {}), ensure_ascii=False)})
            elif typ == "tool_result":
                if normal: items.append({"type":"message", "role":role, "content":normal}); normal=[]
                items.append({"type":"function_call_output", "call_id":block.get("tool_use_id"), "output":content_text(block.get("content", ""))})
        if normal: items.append({"type":"message", "role":role, "content":normal})
    return items

def build_request(body):
    deployment = env("FOUNDRY_DEPLOYMENT")
    if not deployment: raise ValueError("FOUNDRY_DEPLOYMENT is not set")
    payload = {"model": deployment, "input": to_openai_input(body.get("messages")), "store": False}
    system = body.get("system")
    if system:
        payload["instructions"] = content_text(system)
    if body.get("max_tokens") is not None: payload["max_output_tokens"] = body["max_tokens"]
    if body.get("temperature") is not None: payload["temperature"] = body["temperature"]
    tools = []
    for t in body.get("tools", []) or []:
        if t.get("type") in ("custom", "computer_20241022"):
            continue
        if t.get("name"):
            tools.append({"type":"function", "name":t["name"], "description":t.get("description", ""), "parameters":t.get("input_schema", {"type":"object", "properties":{}})})
    if tools: payload["tools"] = tools
    choice = body.get("tool_choice") or {}
    if choice.get("type") == "tool": payload["tool_choice"] = {"type":"function", "name":choice.get("name")}
    elif choice.get("type") in ("any", "auto"): payload["tool_choice"] = "required" if choice["type"] == "any" else "auto"
    return payload

def auth_headers(request_id):
    key = env("FOUNDRY_API_KEY")
    if not key: raise ValueError("FOUNDRY_API_KEY is not set")
    mode = env("FOUNDRY_AUTH_MODE", "api-key").lower()
    headers = {"Content-Type":"application/json", "Accept":"application/json", "x-ms-client-request-id":request_id}
    headers["Authorization" if mode == "bearer" else "api-key"] = ("Bearer " + key) if mode == "bearer" else key
    return headers

def usage(data):
    u = data.get("usage") or {}
    return {"input_tokens":u.get("input_tokens", 0), "output_tokens":u.get("output_tokens", 0)}

def anthropic_response(data, request_id):
    blocks=[]
    for item in data.get("output", []) or []:
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text": blocks.append({"type":"text", "text":c.get("text", "")})
        elif item.get("type") == "function_call":
            try: args=json.loads(item.get("arguments") or "{}")
            except json.JSONDecodeError: args={}
            blocks.append({"type":"tool_use", "id":item.get("call_id") or "toolu_"+uuid.uuid4().hex, "name":item.get("name", ""), "input":args})
    return {"id":"msg_" + data.get("id", request_id), "type":"message", "role":"assistant", "model":env("FOUNDRY_DEPLOYMENT"), "content":blocks or [{"type":"text","text":data.get("output_text", "")}], "stop_reason":"tool_use" if any(b["type"]=="tool_use" for b in blocks) else "end_turn", "stop_sequence":None, "usage":usage(data)}

class Handler(BaseHTTPRequestHandler):
    server_version = "claude-gpt56-proxy/1.0"
    def log_message(self, fmt, *args): LOG.info("client=%s %s", self.client_address[0], fmt % args)
    def send_json(self, code, obj, request_id=None):
        raw=json.dumps(obj, ensure_ascii=False).encode(); self.send_response(code)
        self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(raw)))
        if request_id: self.send_header("request-id",request_id)
        self.end_headers(); self.wfile.write(raw)
    def do_HEAD(self):
        self.send_response(200); self.end_headers()
    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/healthz"):
            self.send_json(200,{"status":"ok","configured":bool(env("FOUNDRY_ENDPOINT") and env("FOUNDRY_DEPLOYMENT") and env("FOUNDRY_API_KEY"))}); return
        if self.path.startswith("/v1/models"):
            self.send_json(200,{"data":[{"id":env("FOUNDRY_DEPLOYMENT", "gpt-5.6-sol"),"type":"model"}]}); return
        self.send_json(404,{"error":{"type":"not_found_error","message":"Not found"}})
    def do_POST(self):
        if self.path.split("?")[0] == "/v1/messages/count_tokens":
            n=int(len(json.dumps(self.read_body(), ensure_ascii=False))/4); self.send_json(200,{"input_tokens":n}); return
        if self.path.split("?")[0] != "/v1/messages": self.send_json(404,{"error":{"type":"not_found_error","message":"Not found"}}); return
        rid="req_"+uuid.uuid4().hex
        try:
            body=self.read_body(); payload=build_request(body)
            if body.get("stream"): self.stream(payload, rid)
            else: self.complete(payload, rid)
        except ValueError as e: self.send_json(503,{"type":"error","error":{"type":"api_error","message":str(e)}},rid)
        except Exception as e:
            LOG.exception("request_id=%s failed", rid); self.send_json(502,{"type":"error","error":{"type":"api_error","message":"Upstream request failed; see local proxy log with request ID " + rid}},rid)
    def read_body(self):
        length=int(self.headers.get("Content-Length","0")); return json.loads(self.rfile.read(length) or b"{}")
    def upstream(self, payload, rid, stream=False):
        payload["stream"]=stream; headers=auth_headers(rid)
        if stream: headers["Accept"]="text/event-stream"
        req=Request(responses_url(), data=json.dumps(payload).encode(), headers=headers, method="POST")
        try: return urlopen(req, timeout=600, context=ssl.create_default_context())
        except HTTPError as e:
            detail=e.read(4096).decode("utf-8","replace"); LOG.warning("request_id=%s upstream_status=%s",rid,e.code); raise ValueError("Foundry returned HTTP %s (request ID %s): %s"%(e.code,rid,detail[:500]))
        except URLError as e: raise ValueError("Cannot reach Foundry (request ID %s): %s"%(rid,e.reason))
    def complete(self,payload,rid):
        with self.upstream(payload,rid) as r: data=json.loads(r.read())
        self.send_json(200,anthropic_response(data,rid),rid)
    def sse(self,event,data):
        self.wfile.write(("event: %s\ndata: %s\n\n"%(event,json.dumps(data,ensure_ascii=False))).encode()); self.wfile.flush()
    def stream(self,payload,rid):
        self.send_response(200); self.send_header("Content-Type","text/event-stream"); self.send_header("Cache-Control","no-cache"); self.send_header("Connection","keep-alive"); self.send_header("request-id",rid); self.end_headers()
        message={"id":"msg_"+rid,"type":"message","role":"assistant","model":env("FOUNDRY_DEPLOYMENT"),"content":[],"stop_reason":None,"stop_sequence":None,"usage":{"input_tokens":0,"output_tokens":0}}
        self.sse("message_start",{"type":"message_start","message":message}); idx=0; active={}; completed=False
        with self.upstream(payload,rid,True) as r:
            event=None
            for raw in r:
                line=raw.decode("utf-8","replace").rstrip("\r\n")
                if line.startswith("event:"): event=line[6:].strip(); continue
                if not line.startswith("data:"): continue
                try: d=json.loads(line[5:].strip())
                except json.JSONDecodeError: continue
                typ=d.get("type",event or "")
                if typ == "response.output_text.delta":
                    if "text" not in active: active["text"]=idx; self.sse("content_block_start",{"type":"content_block_start","index":idx,"content_block":{"type":"text","text":""}}); idx+=1
                    self.sse("content_block_delta",{"type":"content_block_delta","index":active["text"],"delta":{"type":"text_delta","text":d.get("delta","")}})
                elif typ == "response.output_item.added" and (d.get("item") or {}).get("type") == "function_call":
                    item=d["item"]; key=item.get("call_id"); active[key]=idx; self.sse("content_block_start",{"type":"content_block_start","index":idx,"content_block":{"type":"tool_use","id":key,"name":item.get("name",""),"input":{}}}); idx+=1
                elif typ == "response.function_call_arguments.delta":
                    key=d.get("call_id");
                    if key in active: self.sse("content_block_delta",{"type":"content_block_delta","index":active[key],"delta":{"type":"input_json_delta","partial_json":d.get("delta","")}})
                elif typ == "response.output_item.done":
                    item=d.get("item",{}); key=item.get("call_id")
                    if key in active: self.sse("content_block_stop",{"type":"content_block_stop","index":active[key]})
                elif typ == "response.completed":
                    response=d.get("response",{}); message["usage"]=usage(response); completed=True
        if "text" in active: self.sse("content_block_stop",{"type":"content_block_stop","index":active["text"]})
        stop_reason="tool_use" if any(k != "text" for k in active) else "end_turn"
        self.sse("message_delta",{"type":"message_delta","delta":{"stop_reason":stop_reason,"stop_sequence":None},"usage":message["usage"]})
        self.sse("message_stop",{"type":"message_stop"})

if __name__ == "__main__":
    logging.basicConfig(level=getattr(logging,env("LOG_LEVEL","INFO").upper(),logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    host=env("PROXY_HOST","127.0.0.1"); port=int(env("PROXY_PORT","8787"))
    LOG.info("listening on http://%s:%s (Foundry credentials are not logged)",host,port)
    ThreadingHTTPServer((host,port),Handler).serve_forever()
