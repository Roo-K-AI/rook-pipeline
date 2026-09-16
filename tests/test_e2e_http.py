import json
import threading
import time
import unittest
import urllib.request
import urllib.error

import uvicorn
from app.main import app
from app.services.job_registry import get_job_registry


class TestE2EHttp(unittest.TestCase):
    server = None
    server_thread = None
    port = 8765

    @classmethod
    def setUpClass(cls) -> None:
        config = uvicorn.Config(app=app, host="127.0.0.1", port=cls.port, log_level="warning")
        cls.server = uvicorn.Server(config=config)
        cls.server_thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.server:
            cls.server.should_exit = True
            cls.server_thread.join(timeout=2)

    def setUp(self) -> None:
        get_job_registry().clear()

    def test_post_and_get_job_e2e(self) -> None:
        base_url = f"http://127.0.0.1:{self.port}"

        # 1. Healthcheck
        with urllib.request.urlopen(f"{base_url}/health") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["status"], "ok")

        # 2. POST create enrichment job
        post_url = f"{base_url}/internal/v1/product-enrichments"
        payload = {
            "schema_version": "product-enrichment-request.v1",
            "product_name": "Litière à chat agglomérante",
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer test-service-token",
            "X-Correlation-Id": "corr_http_test_1",
            "Idempotency-Key": "idem_http_test_1",
        }
        req = urllib.request.Request(
            post_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 202)
            ack = json.loads(resp.read().decode())
            self.assertEqual(ack["schema_version"], "product-enrichment-ack.v1")
            self.assertEqual(ack["status"], "accepted")
            self.assertEqual(ack["current_step"], "queued")
            job_id = ack["job_id"]
            status_url = ack["status_url"]

        # 3. GET job state
        get_url = f"{base_url}{status_url}"
        get_req = urllib.request.Request(get_url, method="GET")
        with urllib.request.urlopen(get_req) as resp:
            self.assertEqual(resp.status, 200)
            status_data = json.loads(resp.read().decode())
            self.assertEqual(status_data["job_id"], job_id)
            self.assertEqual(status_data["status"], "accepted")
            self.assertEqual(status_data["current_step"], "queued")
            self.assertIsNone(status_data.get("result"))
            self.assertIn(status_data["status"], ["accepted", "queued", "collecting", "extracting", "normalizing", "building_context", "generating", "validating", "completed", "needs_review"])
            self.assertIn(status_data["status"], ["accepted", "queued", "generating", "completed", "needs_review", "failed"])

        # Attendre la fin de l'orchestration asynchrone
        # Attendre que le job soit traité
        for _ in range(40):
            with urllib.request.urlopen(get_req) as resp:
                status_data = json.loads(resp.read().decode())
                if status_data["status"] in ("completed", "needs_review"):
                if status_data["status"] in ("completed", "needs_review", "failed"):
                    break
            time.sleep(0.05)

        self.assertIn(status_data["status"], ("completed", "needs_review"))
        self.assertIsNotNone(status_data.get("result"))
        self.assertIn(status_data["status"], ("accepted", "queued", "generating", "completed", "needs_review", "failed"))

        # 4. GET non-existing job -> 404
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"{base_url}/internal/v1/product-enrichments/jobs/job_inexistant")
        self.assertEqual(ctx.exception.code, 404)

        # 5. POST with same idempotency key and same payload -> returns existing job
        req2 = urllib.request.Request(
            post_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req2) as resp:
            self.assertEqual(resp.status, 202)
            ack2 = json.loads(resp.read().decode())
            self.assertEqual(ack2["job_id"], job_id)

        # 6. POST with same idempotency key but different payload -> 409 Conflict
        conflict_payload = {
            "schema_version": "product-enrichment-request.v1",
            "product_name": "Arbre à chat géant",
        }
        req_conflict = urllib.request.Request(
            post_url,
            data=json.dumps(conflict_payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req_conflict)
        self.assertEqual(ctx.exception.code, 409)

    def test_ready_endpoint(self) -> None:
        base_url = f"http://127.0.0.1:{self.port}"

        with urllib.request.urlopen(f"{base_url}/ready") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["status"], "ready")

    def test_post_missing_required_headers_returns_422(self) -> None:
        base_url = f"http://127.0.0.1:{self.port}"
        payload = {
            "schema_version": "product-enrichment-request.v1",
            "product_name": "Produit valide",
        }
        req = urllib.request.Request(
            f"{base_url}/internal/v1/product-enrichments",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer test-service-token"},
            method="POST",
        )

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 422)

        error = json.loads(ctx.exception.read().decode())
        self.assertEqual(error["detail"], "request_validation_failed")

    def test_post_invalid_payload_returns_422(self) -> None:
        base_url = f"http://127.0.0.1:{self.port}"
        payload = {
            "schema_version": "product-enrichment-request.v2",
            "product_name": "X",
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer test-service-token",
            "X-Correlation-Id": "corr_http_invalid_payload",
            "Idempotency-Key": "idem_http_invalid_payload",
        }
        req = urllib.request.Request(
            f"{base_url}/internal/v1/product-enrichments",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 422)

        error = json.loads(ctx.exception.read().decode())
        self.assertIn("detail", error)

    def test_post_missing_authorization_returns_401(self) -> None:
        base_url = f"http://127.0.0.1:{self.port}"
        payload = {
            "schema_version": "product-enrichment-request.v1",
            "product_name": "Produit valide",
        }
        headers = {
            "Content-Type": "application/json",
            "X-Correlation-Id": "corr_http_missing_auth",
            "Idempotency-Key": "idem_http_missing_auth",
        }
        req = urllib.request.Request(
            f"{base_url}/internal/v1/product-enrichments",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 401)

        error = json.loads(ctx.exception.read().decode())
        self.assertEqual(error["detail"], "service_authentication_failed")


if __name__ == "__main__":
    unittest.main()

