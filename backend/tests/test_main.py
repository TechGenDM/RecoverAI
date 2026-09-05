import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import app

client = TestClient(app)


def test_health_check_returns_database_status():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "recoverai"
    assert data["database"] == "connected"


def test_config_masks_secrets():
    # Verify that secrets are wrapped in SecretStr and not exposed in str/repr
    from pydantic import SecretStr

    test_settings = Settings(
        RAZORPAY_KEY_SECRET=SecretStr("super_secret_rzp_key"),
        RAZORPAY_WEBHOOK_SECRET=SecretStr("super_secret_wh_key"),
        GEMINI_API_KEY=SecretStr("super_secret_gemini_key"),
    )
    repr_str = repr(test_settings)
    assert "super_secret_rzp_key" not in repr_str
    assert "super_secret_wh_key" not in repr_str
    assert "super_secret_gemini_key" not in repr_str

    # Verify safe_dict helper masks values
    safe = test_settings.safe_dict()
    assert safe["RAZORPAY_KEY_SECRET_SET"] is True
    assert safe["RAZORPAY_WEBHOOK_SECRET_SET"] is True
    assert safe["GEMINI_API_KEY_SET"] is True
    assert "super_secret_rzp_key" not in str(safe)


def test_config_bounds_validation():
    # Bounds: RECOVERY_MAX_ATTEMPTS must be between 1 and 10
    with pytest.raises(ValidationError):
        Settings(RECOVERY_MAX_ATTEMPTS=0)

    with pytest.raises(ValidationError):
        Settings(RECOVERY_MAX_ATTEMPTS=100)

    # Window bounds: 1 to 168 hours
    with pytest.raises(ValidationError):
        Settings(RECOVERY_MAX_WINDOW_HOURS=0)

    # Link expiry bounds: 1 to 72 hours
    with pytest.raises(ValidationError):
        Settings(RECOVERY_LINK_EXPIRY_HOURS=200)


def test_config_rejects_live_razorpay_key():
    with pytest.raises(ValidationError, match="must begin with 'rzp_test_'"):
        Settings(RAZORPAY_KEY_ID="rzp_live_1234567890abcdef")


def test_config_accepts_test_razorpay_key():
    s = Settings(RAZORPAY_KEY_ID="rzp_test_1234567890abcdef")
    assert s.RAZORPAY_KEY_ID == "rzp_test_1234567890abcdef"
    assert s.safe_dict()["RAZORPAY_KEY_ID_MASKED"] == "rzp_test****cdef"


def test_webhook_endpoint_routing_and_method_restriction():
    # GET /v1/webhooks/razorpay is not permitted (must be POST)
    resp_get = client.get("/v1/webhooks/razorpay")
    assert resp_get.status_code == 405

    # POST /v1/webhooks/razorpay without required headers rejects safely with 400
    resp_post = client.post("/v1/webhooks/razorpay", json={"event": "ping"})
    assert resp_post.status_code == 400
    assert resp_post.json()["error"] == "missing_signature"
