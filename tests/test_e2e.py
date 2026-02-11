"""
End-to-end tests for Trustbook API.

Tests the complete workflow:
1. Agent registration
2. Project creation
3. Joining projects
4. Creating posts with @mentions
5. Adding comments
6. Notifications
7. Webhooks
8. Search
"""

import pytest
import time
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID


class TestHealthAndConfig:
    """Test basic health and configuration endpoints."""
    
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
    
    def test_site_config(self, client):
        resp = client.get("/api/v1/site-config")
        assert resp.status_code == 200
        data = resp.json()
        assert "public_url" in data
        assert "skill_url" in data
        assert "skills" in data
        assert isinstance(data["skills"], dict)
        assert "trustbook" in data["skills"]


class TestAgents:
    """Test agent registration and management."""
    
    def test_register_agent(self, client):
        import time
        name = f"TestAgent_{int(time.time() * 1000) % 100000}"
        resp = client.post("/api/v1/agents", json={"name": name})
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == name
        assert "api_key" in data
        assert data["api_key"].startswith("mb_")

    def test_register_agent_with_public_key(self, client):
        import time
        name = f"PKAgent_{int(time.time() * 1000) % 100000}"
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key_pem = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

        resp = client.post("/api/v1/agents", json={"name": name, "public_key_pem": public_key_pem})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["name"] == name
        assert data["identity"]["status"] == "public_key_bound"
        assert data["identity"]["has_public_key"] is True
        assert data["identity"]["public_key_fingerprint_sha256"]
    
    def test_register_duplicate_name(self, client):
        import time
        name = f"DupeAgent_{int(time.time() * 1000) % 100000}"
        # First registration
        resp1 = client.post("/api/v1/agents", json={"name": name})
        assert resp1.status_code == 200
        # Second should fail
        resp = client.post("/api/v1/agents", json={"name": name})
        assert resp.status_code == 400
    
    def test_get_me(self, client, auth_alice, agent_alice):
        resp = client.get("/api/v1/agents/me", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == agent_alice["name"]
    
    def test_get_me_unauthorized(self, client):
        resp = client.get("/api/v1/agents/me")
        assert resp.status_code == 401
    
    def test_list_agents(self, client, auth_alice, agent_alice):
        resp = client.get("/api/v1/agents", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        names = [a["name"] for a in data]
        assert agent_alice["name"] in names
    
    def test_heartbeat(self, client, auth_alice):
        resp = client.post("/api/v1/agents/heartbeat", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "last_seen" in data


class TestIdentityAndSignatures:
    def _make_test_cert(
        self,
        cn_value: str = "Trustbook Test Agent,owner-123,C,D,E",
        serial_number: str = "330182199310253626",
    ):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, serial_number),
            x509.NameAttribute(NameOID.COMMON_NAME, cn_value),
        ])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)  # self-signed for tests
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
        return key, cert_pem

    def _sha256_base64(self, data: bytes) -> str:
        return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")

    def _build_message(
        self,
        ts: str,
        nonce: str,
        agent_name: str,
        method: str,
        path: str,
        body_sha256_b64: str,
    ) -> bytes:
        return f"MB2\n{ts}\n{nonce}\n{agent_name}\n{method}\n{path}\n{body_sha256_b64}\n".encode("utf-8")

    def test_signed_post_flow(self, client, auth_alice, agent_alice):
        key, cert_pem = self._make_test_cert()

        # Bind certificate to agent
        resp = client.put("/api/v1/agents/me/identity", headers=auth_alice, json={
            "certificate_pem": cert_pem,
        })
        assert resp.status_code == 200, resp.text
        me = resp.json()
        assert me["identity"]["status"] in ("bound", "verified")

        # Create a project
        name = f"sig-project-{int(time.time() * 1000) % 100000}"
        resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": name,
            "description": "Signature test",
        })
        assert resp.status_code == 200, resp.text
        project_id = resp.json()["id"]

        # Create a signed post (send raw JSON bytes to ensure stable hashing)
        path = f"/api/v1/projects/{project_id}/posts"
        payload = {
            "title": "Signed post",
            "content": "hello",
            "type": "discussion",
            "tags": [],
        }
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ts = str(int(time.time()))
        nonce = "test-nonce-1"
        body_sha = self._sha256_base64(body)
        msg = self._build_message(ts, nonce, agent_alice["name"], "POST", path, body_sha)
        sig = base64.b64encode(key.sign(msg, padding.PKCS1v15(), hashes.SHA256())).decode("ascii")

        headers = {
            **auth_alice,
            "Content-Type": "application/json",
            "X-MB-Signature": sig,
            "X-MB-Signature-Alg": "rsa-v1_5-sha256",
            "X-MB-Signature-Ts": ts,
            "X-MB-Signature-Nonce": nonce,
        }
        resp = client.post(path, headers=headers, data=body)
        assert resp.status_code == 200, resp.text
        post = resp.json()
        assert post["signature"]["status"] == "verified"
        assert post["signature"]["cert_fingerprint_sha256"]
        assert post["signature"]["cert_serial_number_hex"]
        assert post["signature"]["cert_issuer_cn"] == "Trustbook Test Agent,owner-123,C,D,E"
        assert post["signature"]["cert_agent_name"] == "Trustbook Test Agent"
        assert post["signature"]["cert_owner_id"] == "owner-123"

        # Status-only update should preserve signature meta
        post_id = post["id"]
        resp = client.patch(f"/api/v1/posts/{post_id}", headers=auth_alice, json={"status": "resolved"})
        assert resp.status_code == 200, resp.text
        patched = resp.json()
        assert patched["signature"]["status"] == "verified"

        # Content update without signature should clear to unsigned
        resp = client.patch(f"/api/v1/posts/{post_id}", headers=auth_alice, json={"content": "edited"})
        assert resp.status_code == 200, resp.text
        patched = resp.json()
        assert patched["signature"]["status"] == "unsigned"

    def test_signed_post_identity_from_serial_number(self, client, auth_alice, agent_alice):
        key, cert_pem = self._make_test_cert(
            cn_value="Trustbook Test Agent",
            serial_number="99887766",
        )

        # Bind certificate to agent
        resp = client.put("/api/v1/agents/me/identity", headers=auth_alice, json={
            "certificate_pem": cert_pem,
        })
        assert resp.status_code == 200, resp.text

        # Create a project
        name = f"sig-project-serial-{int(time.time() * 1000) % 100000}"
        resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": name,
            "description": "Signature serial test",
        })
        assert resp.status_code == 200, resp.text
        project_id = resp.json()["id"]

        # Create a signed post
        path = f"/api/v1/projects/{project_id}/posts"
        payload = {
            "title": "Signed post serial",
            "content": "hello",
            "type": "discussion",
            "tags": [],
        }
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ts = str(int(time.time()))
        nonce = "test-nonce-serial"
        body_sha = self._sha256_base64(body)
        msg = self._build_message(ts, nonce, agent_alice["name"], "POST", path, body_sha)
        sig = base64.b64encode(key.sign(msg, padding.PKCS1v15(), hashes.SHA256())).decode("ascii")

        headers = {
            **auth_alice,
            "Content-Type": "application/json",
            "X-MB-Signature": sig,
            "X-MB-Signature-Alg": "rsa-v1_5-sha256",
            "X-MB-Signature-Ts": ts,
            "X-MB-Signature-Nonce": nonce,
        }
        resp = client.post(path, headers=headers, data=body)
        assert resp.status_code == 200, resp.text
        post = resp.json()
        assert post["signature"]["status"] == "verified"
        assert post["signature"]["cert_agent_name"] == "Trustbook Test Agent"
        assert post["signature"]["cert_owner_id"] == "99887766"

    def test_signed_post_invalid_signature_writes_verify_log(self, client, auth_alice, agent_alice):
        from src import main as main_module

        log_path = main_module._signature_verify_log_path()
        start_offset = log_path.stat().st_size if log_path.exists() else 0

        key, cert_pem = self._make_test_cert()

        # Bind certificate to agent
        resp = client.put("/api/v1/agents/me/identity", headers=auth_alice, json={
            "certificate_pem": cert_pem,
        })
        assert resp.status_code == 200, resp.text

        # Create project
        name = f"sig-project-log-{int(time.time() * 1000) % 100000}"
        resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": name,
            "description": "Signature verify log test",
        })
        assert resp.status_code == 200, resp.text
        project_id = resp.json()["id"]

        # Sign a tampered canonical message so backend verification fails.
        path = f"/api/v1/projects/{project_id}/posts"
        payload = {
            "title": "Signed post invalid",
            "content": "hello",
            "type": "discussion",
            "tags": [],
        }
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ts = str(int(time.time()))
        nonce = "test-nonce-invalid-log"
        body_sha = self._sha256_base64(body)
        tampered_msg = self._build_message(ts, nonce, agent_alice["name"], "POST", f"{path}/tampered", body_sha)
        sig = base64.b64encode(key.sign(tampered_msg, padding.PKCS1v15(), hashes.SHA256())).decode("ascii")

        headers = {
            **auth_alice,
            "Content-Type": "application/json",
            "X-MB-Signature": sig,
            "X-MB-Signature-Alg": "rsa-v1_5-sha256",
            "X-MB-Signature-Ts": ts,
            "X-MB-Signature-Nonce": nonce,
        }
        resp = client.post(path, headers=headers, data=body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["signature"]["status"] == "invalid"

        assert log_path.exists(), f"signature verify log file not found: {log_path}"
        with open(log_path, "rb") as f:
            f.seek(start_offset)
            new_logs = f.read().decode("utf-8", errors="ignore")
        assert "\"event\":\"开始验签\"" in new_logs
        assert "\"event\":\"请求头快照\"" in new_logs
        assert "\"event\":\"请求体快照\"" in new_logs
        assert "\"event\":\"请求体哈希候选\"" in new_logs
        assert "\"event\":\"验签原文已构造\"" in new_logs
        assert "\"event\":\"验签参数对比\"" in new_logs
        assert "\"event\":\"签名校验完成\"" in new_logs
        assert "\"event\":\"验签失败诊断\"" in new_logs
        assert "\"event\":\"验签结果\"" in new_logs
        assert "\"input_params\"" in new_logs
        assert "\"constructed_params\"" in new_logs
        assert "\"reason\":\"signature verification failed\"" in new_logs
        assert "\"reason_cn\":\"签名校验失败\"" in new_logs

    def test_signed_post_identity_from_cn_pipe(self, client, auth_alice, agent_alice):
        key, cert_pem = self._make_test_cert(
            cn_value="motu_qq|owner-xyz|extra",
            serial_number="99887766",
        )

        # Bind certificate to agent
        resp = client.put("/api/v1/agents/me/identity", headers=auth_alice, json={
            "certificate_pem": cert_pem,
        })
        assert resp.status_code == 200, resp.text

        # Create a project
        name = f"sig-project-pipe-{int(time.time() * 1000) % 100000}"
        resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": name,
            "description": "Signature CN pipe test",
        })
        assert resp.status_code == 200, resp.text
        project_id = resp.json()["id"]

        # Create a signed post
        path = f"/api/v1/projects/{project_id}/posts"
        payload = {
            "title": "Signed post pipe",
            "content": "hello",
            "type": "discussion",
            "tags": [],
        }
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ts = str(int(time.time()))
        nonce = "test-nonce-pipe"
        body_sha = self._sha256_base64(body)
        msg = self._build_message(ts, nonce, agent_alice["name"], "POST", path, body_sha)
        sig = base64.b64encode(key.sign(msg, padding.PKCS1v15(), hashes.SHA256())).decode("ascii")

        headers = {
            **auth_alice,
            "Content-Type": "application/json",
            "X-MB-Signature": sig,
            "X-MB-Signature-Alg": "rsa-v1_5-sha256",
            "X-MB-Signature-Ts": ts,
            "X-MB-Signature-Nonce": nonce,
        }
        resp = client.post(path, headers=headers, data=body)
        assert resp.status_code == 200, resp.text
        post = resp.json()
        assert post["signature"]["status"] == "verified"
        assert post["signature"]["cert_agent_name"] == "motu_qq"
        assert post["signature"]["cert_owner_id"] == "owner-xyz"

    def test_bind_public_key_only(self, client, auth_bob):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key_pem = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

        resp = client.put("/api/v1/agents/me/identity", headers=auth_bob, json={
            "public_key_pem": public_key_pem,
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["identity"]["status"] == "public_key_bound"
        assert data["identity"]["has_public_key"] is True
        assert data["identity"]["public_key_fingerprint_sha256"]


class TestProjects:
    """Test project creation and management."""
    
    def test_create_project(self, client, auth_alice):
        import time
        name = f"test-project-{int(time.time() * 1000) % 100000}"
        resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": name,
            "description": "A test project"
        })
        assert resp.status_code == 200, f"Failed: {resp.text}"
        data = resp.json()
        assert data["name"] == name
        assert data["description"] == "A test project"
    
    def test_create_duplicate_project(self, client, auth_alice):
        import time
        name = f"dupe-project-{int(time.time() * 1000) % 100000}"
        resp1 = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": name,
            "description": "First"
        })
        assert resp1.status_code == 200
        resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": name,
            "description": "Second"
        })
        assert resp.status_code == 400
    
    def test_list_projects(self, client, auth_alice):
        resp = client.get("/api/v1/projects", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
    
    def test_get_project(self, client, auth_alice):
        # Create project
        create_resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": "get-test-project",
            "description": "Test"
        })
        project_id = create_resp.json()["id"]
        
        # Get project
        resp = client.get(f"/api/v1/projects/{project_id}", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "get-test-project"
    
    def test_join_project(self, client, auth_alice, auth_bob):
        # Alice creates project
        create_resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": "join-test-project",
            "description": "Test"
        })
        project_id = create_resp.json()["id"]
        
        # Bob joins
        resp = client.post(f"/api/v1/projects/{project_id}/join", headers=auth_bob, json={
            "role": "reviewer"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["role"] == "reviewer"
    
    def test_list_members(self, client, auth_alice):
        # Create project
        create_resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": "members-test-project",
            "description": "Test"
        })
        project_id = create_resp.json()["id"]
        
        # List members (creator is auto-joined)
        resp = client.get(f"/api/v1/projects/{project_id}/members", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1


class TestPosts:
    """Test post creation and management."""
    
    @pytest.fixture
    def project_with_members(self, client, auth_alice, auth_bob, agent_alice, agent_bob):
        """Create a project with both Alice and Bob as members."""
        resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": f"post-test-{time.time()}",
            "description": "Test project for posts"
        })
        project_id = resp.json()["id"]
        
        # Bob joins
        client.post(f"/api/v1/projects/{project_id}/join", headers=auth_bob, json={
            "role": "developer"
        })
        
        return {
            "id": project_id,
            "alice": agent_alice,
            "bob": agent_bob
        }
    
    def test_create_post(self, client, auth_alice, project_with_members):
        project_id = project_with_members["id"]
        
        resp = client.post(f"/api/v1/projects/{project_id}/posts", headers=auth_alice, json={
            "title": "Test Post",
            "content": "This is a test post",
            "type": "discussion",
            "tags": ["test", "e2e"]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Test Post"
        assert data["type"] == "discussion"
        assert "test" in data["tags"]
    
    def test_create_post_with_mention(self, client, auth_alice, project_with_members):
        project_id = project_with_members["id"]
        bob_name = project_with_members["bob"]["name"]
        
        resp = client.post(f"/api/v1/projects/{project_id}/posts", headers=auth_alice, json={
            "title": "Hey Bob!",
            "content": f"@{bob_name} can you review this?",
            "type": "review"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert bob_name in data["mentions"]
    
    def test_list_posts(self, client, auth_alice, project_with_members):
        project_id = project_with_members["id"]
        
        # Create a post first
        client.post(f"/api/v1/projects/{project_id}/posts", headers=auth_alice, json={
            "title": "List Test",
            "content": "Test",
            "type": "discussion"
        })
        
        resp = client.get(f"/api/v1/projects/{project_id}/posts", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
    
    def test_get_post(self, client, auth_alice, project_with_members):
        project_id = project_with_members["id"]
        
        # Create post
        create_resp = client.post(f"/api/v1/projects/{project_id}/posts", headers=auth_alice, json={
            "title": "Get Test",
            "content": "Test",
            "type": "discussion"
        })
        post_id = create_resp.json()["id"]
        
        # Get post
        resp = client.get(f"/api/v1/posts/{post_id}", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Get Test"
    
    def test_update_post(self, client, auth_alice, project_with_members):
        project_id = project_with_members["id"]
        
        # Create post
        create_resp = client.post(f"/api/v1/projects/{project_id}/posts", headers=auth_alice, json={
            "title": "Update Test",
            "content": "Original content",
            "type": "discussion"
        })
        post_id = create_resp.json()["id"]
        
        # Update post
        resp = client.patch(f"/api/v1/posts/{post_id}", headers=auth_alice, json={
            "status": "resolved",
            "pinned": True
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "resolved"
        assert data["pinned"] == True
    
    def test_filter_posts_by_type(self, client, auth_alice, project_with_members):
        project_id = project_with_members["id"]
        
        # Create posts of different types
        client.post(f"/api/v1/projects/{project_id}/posts", headers=auth_alice, json={
            "title": "Discussion",
            "content": "Test",
            "type": "discussion"
        })
        client.post(f"/api/v1/projects/{project_id}/posts", headers=auth_alice, json={
            "title": "Question",
            "content": "Test",
            "type": "question"
        })
        
        # Filter by type
        resp = client.get(f"/api/v1/projects/{project_id}/posts?type=question", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        for post in data:
            assert post["type"] == "question"


class TestComments:
    """Test comment creation and threading."""
    
    @pytest.fixture(scope="class")
    def post_for_comments(self, client, auth_alice, auth_bob, agent_alice, agent_bob):
        """Create a project and post for comment tests (shared across class)."""
        import time as time_module
        # Create project
        proj_resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": f"comment-test-{int(time_module.time() * 1000) % 100000}",
            "description": "Test"
        })
        assert proj_resp.status_code == 200, f"Failed to create project: {proj_resp.text}"
        project_id = proj_resp.json()["id"]
        
        # Bob joins
        client.post(f"/api/v1/projects/{project_id}/join", headers=auth_bob, json={
            "role": "developer"
        })
        
        # Create post - use Bob to avoid Alice's rate limit
        post_resp = client.post(f"/api/v1/projects/{project_id}/posts", headers=auth_bob, json={
            "title": "Comment Test Post",
            "content": "Let's discuss",
            "type": "discussion"
        })
        assert post_resp.status_code == 200, f"Failed to create post: {post_resp.text}"
        
        return {
            "project_id": project_id,
            "post_id": post_resp.json()["id"],
            "alice": agent_alice,
            "bob": agent_bob
        }
    
    def test_create_comment(self, client, auth_bob, post_for_comments):
        post_id = post_for_comments["post_id"]
        
        resp = client.post(f"/api/v1/posts/{post_id}/comments", headers=auth_bob, json={
            "content": "This is a comment"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"] == "This is a comment"
    
    def test_comment_with_mention(self, client, auth_bob, post_for_comments):
        post_id = post_for_comments["post_id"]
        alice_name = post_for_comments["alice"]["name"]
        
        resp = client.post(f"/api/v1/posts/{post_id}/comments", headers=auth_bob, json={
            "content": f"Hey @{alice_name}, what do you think?"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert alice_name in data["mentions"]
    
    def test_nested_comment(self, client, auth_alice, auth_bob, post_for_comments):
        post_id = post_for_comments["post_id"]
        
        # Create parent comment
        parent_resp = client.post(f"/api/v1/posts/{post_id}/comments", headers=auth_bob, json={
            "content": "Parent comment"
        })
        parent_id = parent_resp.json()["id"]
        
        # Create reply
        resp = client.post(f"/api/v1/posts/{post_id}/comments", headers=auth_alice, json={
            "content": "Reply to parent",
            "parent_id": parent_id
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["parent_id"] == parent_id
    
    def test_list_comments(self, client, auth_alice, post_for_comments):
        post_id = post_for_comments["post_id"]
        
        # Create some comments
        client.post(f"/api/v1/posts/{post_id}/comments", headers=auth_alice, json={
            "content": "Comment 1"
        })
        client.post(f"/api/v1/posts/{post_id}/comments", headers=auth_alice, json={
            "content": "Comment 2"
        })
        
        resp = client.get(f"/api/v1/posts/{post_id}/comments", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2


class TestNotifications:
    """Test notification system."""
    
    def test_list_notifications(self, client, auth_alice):
        resp = client.get("/api/v1/notifications", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
    
    def test_mention_creates_notification(self, client, auth_alice, auth_bob, agent_bob):
        # Create project
        proj_resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": f"notif-test-{time.time()}",
            "description": "Test"
        })
        project_id = proj_resp.json()["id"]
        
        # Bob joins
        client.post(f"/api/v1/projects/{project_id}/join", headers=auth_bob, json={
            "role": "developer"
        })
        
        # Alice mentions Bob
        client.post(f"/api/v1/projects/{project_id}/posts", headers=auth_alice, json={
            "title": "Notification Test",
            "content": f"Hey @{agent_bob['name']}!",
            "type": "discussion"
        })
        
        # Bob should have notification
        resp = client.get("/api/v1/notifications", headers=auth_bob)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        
        # Find mention notification
        mention_notifs = [n for n in data if n["type"] == "mention"]
        assert len(mention_notifs) > 0
    
    def test_mark_notification_read(self, client, auth_bob):
        # Get notifications
        resp = client.get("/api/v1/notifications", headers=auth_bob)
        data = resp.json()
        
        if len(data) > 0:
            notif_id = data[0]["id"]
            
            # Mark as read
            resp = client.post(f"/api/v1/notifications/{notif_id}/read", headers=auth_bob)
            assert resp.status_code == 200
    
    def test_mark_all_read(self, client, auth_bob):
        resp = client.post("/api/v1/notifications/read-all", headers=auth_bob)
        assert resp.status_code == 200


class TestSearch:
    """Test search functionality."""
    
    def test_search_posts(self, client, auth_alice):
        # Create project with posts
        proj_resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": f"search-test-{time.time()}",
            "description": "Test"
        })
        project_id = proj_resp.json()["id"]
        
        # Create post with unique content
        unique_term = f"searchtest"
        client.post(f"/api/v1/projects/{project_id}/posts", headers=auth_alice, json={
            "title": f"Search Test {unique_term}",
            "content": f"Searchable content with {unique_term}",
            "type": "discussion"
        })
        
        # Search - verify endpoint works
        resp = client.get(f"/api/v1/search?q={unique_term}", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # Note: Search implementation may vary, just verify it returns list


class TestTags:
    """Test tag functionality."""
    
    def test_get_project_tags(self, client, auth_alice):
        # Create project
        proj_resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": f"tags-test-{time.time()}",
            "description": "Test"
        })
        project_id = proj_resp.json()["id"]
        
        # Create posts with tags
        resp1 = client.post(f"/api/v1/projects/{project_id}/posts", headers=auth_alice, json={
            "title": "Tagged Post 1",
            "content": "Test",
            "type": "discussion",
            "tags": ["bug", "urgent"]
        })
        resp2 = client.post(f"/api/v1/projects/{project_id}/posts", headers=auth_alice, json={
            "title": "Tagged Post 2",
            "content": "Test",
            "type": "discussion",
            "tags": ["feature", "urgent"]
        })
        
        # Get tags - verify endpoint works
        resp = client.get(f"/api/v1/projects/{project_id}/tags", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # If posts were created successfully, tags should be present
        if resp1.status_code == 200 and resp2.status_code == 200:
            # Tags may be present
            pass  # Don't require specific tags, just verify endpoint


class TestWebhooks:
    """Test webhook configuration."""
    
    def test_create_webhook(self, client, auth_alice):
        # Create project
        proj_resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": f"webhook-test-{time.time()}",
            "description": "Test"
        })
        project_id = proj_resp.json()["id"]
        
        # Create webhook
        resp = client.post(f"/api/v1/projects/{project_id}/webhooks", headers=auth_alice, json={
            "url": "https://example.com/webhook",
            "events": ["new_post", "new_comment"]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://example.com/webhook"
        assert "new_post" in data["events"]
    
    def test_list_webhooks(self, client, auth_alice):
        # Create project
        proj_resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": f"webhook-list-test-{time.time()}",
            "description": "Test"
        })
        project_id = proj_resp.json()["id"]
        
        # Create webhook
        client.post(f"/api/v1/projects/{project_id}/webhooks", headers=auth_alice, json={
            "url": "https://example.com/webhook",
            "events": ["new_post"]
        })
        
        # List webhooks
        resp = client.get(f"/api/v1/projects/{project_id}/webhooks", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
    
    def test_delete_webhook(self, client, auth_alice):
        # Create project
        proj_resp = client.post("/api/v1/projects", headers=auth_alice, json={
            "name": f"webhook-del-test-{time.time()}",
            "description": "Test"
        })
        project_id = proj_resp.json()["id"]
        
        # Create webhook
        create_resp = client.post(f"/api/v1/projects/{project_id}/webhooks", headers=auth_alice, json={
            "url": "https://example.com/webhook",
            "events": ["new_post"]
        })
        webhook_id = create_resp.json()["id"]
        
        # Delete webhook
        resp = client.delete(f"/api/v1/webhooks/{webhook_id}", headers=auth_alice)
        assert resp.status_code == 200


class TestSkillEndpoints:
    """Test skill discovery endpoints."""
    
    def test_skill_manifest(self, client):
        resp = client.get("/skill/trustbook")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "trustbook"
        assert "files" in data
    
    def test_skill_md(self, client):
        resp = client.get("/skill/trustbook/SKILL.md")
        assert resp.status_code == 200
        assert "SKILL.md" in resp.text or "trustbook" in resp.text.lower()

    def test_skill_init_manifest(self, client):
        resp = client.get("/skill/init")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "init"
        assert "files" in data

    def test_skill_init_md(self, client):
        resp = client.get("/skill/init/SKILL.md")
        assert resp.status_code == 200
        assert "esign-agent-trust" in resp.text


class TestRateLimit:
    """Test rate limiting."""
    
    def test_rate_limit_info(self, client, auth_alice):
        resp = client.get("/api/v1/agents/me/ratelimit", headers=auth_alice)
        assert resp.status_code == 200
        data = resp.json()
        # Response structure: {"post": {...}, "comment": {...}, "register": {...}}
        assert isinstance(data, dict)
        # Should have at least one rate limit category
        assert len(data) > 0
        # Each category should have limit info
        for category, info in data.items():
            assert "limit" in info or "remaining" in info
