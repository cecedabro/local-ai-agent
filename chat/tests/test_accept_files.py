"""Judge for files in and out over HTTP (chat/SPEC.md §16). Protected: change the code, not this file.

Run: make chat-test K=TestFilesInOut
"""
from __future__ import annotations

import io

from coder import files, tools
from test_accept_core import H, client, gw, invite_user, login, make_client, new_chat, send  # noqa: F401


def upload(c, name, data):
    return c.post("/api/files", files={"file": (name, data, "application/octet-stream")}, headers=H)


class TestFilesInOut:
    def test_upload_kinds_and_errors(self, client):
        login(client)
        pdf = files.render("x", "pdf", "# Uitslag\n\neGFR 22 ml/min.")[0]
        r = upload(client, "../uitslag.pdf", pdf)
        assert r.status_code == 201
        f = r.json()
        assert (f["name"], f["kind"], f["size"]) == ("uitslag.pdf", "pdf", len(pdf)) and f["tokens"] > 0
        assert upload(client, "oud.doc", b"x").json() == {"error": "Save it as .docx or .xlsx first."}
        assert upload(client, "oud.doc", b"x").status_code == 415
        assert upload(client, "scan.pdf", b"%PDF-1.4 nope").status_code == 415
        assert upload(client, "big.txt", b"a" * (files.MAX_UPLOAD + 1)).status_code == 413
        assert client.post("/api/files", files={"file": ("a.txt", b"hi", "text/plain")}).status_code == 403, "X-Coder needed"

    def test_download_headers(self, client):
        login(client)
        docx = files.render("x", "docx", "Hallo")[0]
        fid = upload(client, "Brief ü.docx", docx).json()["id"]
        d = client.get(f"/api/files/{fid}")
        assert d.status_code == 200 and d.content == docx
        assert d.headers["content-type"] == files.MIME["docx"]
        assert d.headers["content-disposition"] == files.disposition("Brief ü.docx")
        assert d.headers["x-content-type-options"] == "nosniff"
        assert d.headers["content-security-policy"] == "default-src 'none'; sandbox"
        other = invite_user(client, "anna", ["chat"])
        assert other.get(f"/api/files/{fid}").status_code == 404

    def test_attached_file_text_goes_to_the_model(self, client, gw):
        login(client)
        docx = files.render("x", "docx", "# Kop\n\nDe inhoud telt.")[0]
        f = upload(client, "notes.docx", docx).json()
        g = upload(client, "code.md", b"Zie ```py\nx\n```").json()
        cid = new_chat(client)
        send(client, cid, "wat staat erin?", files=[f["id"], g["id"]])
        user = gw.chat_requests()[-1]["body"]["messages"][-1]["content"]
        assert user == "wat staat erin?\n\nFile notes.docx:\n```\n# Kop\nDe inhoud telt.\n```" \
                       "\n\nFile code.md:\n````\nZie ```py\nx\n```\n````"
        assert "PK" not in user, "the text, never the raw bytes"
        msg = [m for m in client.get(f"/api/chats/{cid}").json()["messages"] if m["role"] == "user"][0]
        assert msg["files"] == [{"id": f["id"], "name": "notes.docx", "size": len(docx), "kind": "docx"},
                                {"id": g["id"], "name": "code.md", "size": 15, "kind": "text"}]

    def test_the_model_makes_a_file(self, client, gw):
        login(client)
        gw.script = {"tool_calls": [{"name": "make_file", "arguments":
                                     '{"name": "Plan", "format": "docx", "content": "# Weekplan\\n\\n- maandag: rust"}'}],
                     "content": ["Hier is je plan."]}
        cid = new_chat(client)
        evs = send(client, cid, "maak een word bestand met mijn weekplan")
        assert ("phase", {"phase": "writing", "label": "Making Plan…"}) in evs
        made = [d for e, d in evs if e == "file"]
        assert len(made) == 1 and made[0]["name"] == "Plan.docx" and made[0]["kind"] == "docx"
        tool_msg = [m for m in gw.chat_requests()[-1]["body"]["messages"] if m.get("role") == "tool"][0]["content"]
        assert tool_msg.startswith("Made Plan.docx (") and "do not repeat its content" in tool_msg
        done = evs[-1][1]
        assert done["files"] == made
        d = client.get(f"/api/files/{made[0]['id']}")
        import docx as pydocx
        assert [p.text for p in pydocx.Document(io.BytesIO(d.content)).paragraphs][:2] == ["Weekplan", "maandag: rust"]
        ai = [m for m in client.get(f"/api/chats/{cid}").json()["messages"] if m["role"] == "assistant"][0]
        assert ai["files"] == [{"id": made[0]["id"], "name": "Plan.docx", "size": made[0]["size"], "kind": "docx"}]

    def test_export_an_answer(self, client, gw):
        login(client)
        gw.script = {"content": ["# Antwoord\n\nDrink **water**."]}
        cid = new_chat(client)
        mid = send(client, cid, "hoe blijf ik fit?")[-1][1]["message_id"]
        md = client.get(f"/api/messages/{mid}/export?format=md")
        assert md.status_code == 200 and md.content.decode() == "# Antwoord\n\nDrink **water**."
        assert md.headers["content-disposition"].startswith('attachment; filename="') and ".md" in md.headers["content-disposition"]
        pdf = client.get(f"/api/messages/{mid}/export?format=pdf")
        assert pdf.content.startswith(b"%PDF-") and pdf.headers["content-type"] == "application/pdf"
        assert client.get(f"/api/messages/{mid}/export?format=docx").headers["content-type"] == files.MIME["docx"]
        assert b"<strong>water</strong>" in client.get(f"/api/messages/{mid}/export?format=html").content
        assert client.get(f"/api/messages/{mid}/export?format=exe").status_code == 400
        assert client.get("/api/messages/99999/export?format=md").status_code == 404
        other = invite_user(client, "anna", ["chat"])
        assert other.get(f"/api/messages/{mid}/export?format=md").status_code == 404

    def test_export_lists_the_sources(self, tmp_path, gw):
        client = make_client(tmp_path, gw, SEARXNG_URL=gw.url).__enter__()
        login(client)
        gw.search_results = [{"title": "Griepprik", "url": "https://www.rivm.nl/griepprik", "content": "Wie krijgt hem?"}]
        gw.script = {"tool_calls": [{"name": "web_search", "arguments": '{"query": "griepprik"}'}],
                     "content": ["Mensen boven de 60 [1]."]}
        cid = new_chat(client)
        mid = send(client, cid, "griepprik?")[-1][1]["message_id"]
        md = client.get(f"/api/messages/{mid}/export?format=md").content.decode()
        assert md.endswith("## Sources\n\n- [1] Griepprik — https://www.rivm.nl/griepprik")

    def test_a_file_is_a_source_in_clear_source(self, client, gw, monkeypatch):
        login(client)
        monkeypatch.setattr(tools, "PUBMED", gw.url)
        gw.plan = '{"about": "x", "searches": []}'
        text = "Uw eGFR is 22 ml/min en daarom krijgt u een lagere dosis apixaban."
        f = upload(client, "brief.txt", text.encode()).json()
        gw.script = {"content": [f'## Kort antwoord\n\nLagere dosis.\n\n> "{text}" [1]']}
        cid = new_chat(client, "evidence")
        evs = send(client, cid, "Wat betekent mijn brief?", files=[f["id"]])
        assert ("phase", {"phase": "reading", "label": "Reading your file brief.txt…"}) in evs
        plan_call = [r for r in gw.chat_requests() if r["body"]["messages"][0]["content"].startswith("You plan")][-1]
        assert "eGFR is 22" not in plan_call["body"]["messages"][-1]["content"], "the plan gets the typed question only"
        done = evs[-1][1]
        assert done["sources"][0]["kind"] == "file" and done["sources"][0]["title"] == "brief.txt"
        assert done["verify"]["ok"] is True
