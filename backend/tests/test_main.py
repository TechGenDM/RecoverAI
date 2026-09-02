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
    test_settings = Settings(
        RAZORPAY_KEY_SECRET="super_secret_rzp_key",
        RAZORPAY_WEBHOOK_SECRET="super_secret_wh_key",
        GEMINI_API_KEY="super_secret_gemini_key",
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
