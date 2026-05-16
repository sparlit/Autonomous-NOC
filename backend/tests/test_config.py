"""
Tests for backend/app/core/config.py

The PR changed the Settings class from using pydantic ConfigDict to an inner
Config class.  These tests verify default values and that the Settings class
is correctly structured.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import Settings, settings


class TestSettingsDefaults:
    def test_project_name_default(self):
        s = Settings()
        assert s.PROJECT_NAME == "Autonomous NOC API"

    def test_prometheus_url_default(self):
        s = Settings()
        assert s.PROMETHEUS_URL == "http://prometheus:9090"

    def test_keep_url_default(self):
        s = Settings()
        assert s.KEEP_URL == "http://keep:8080"

    def test_settings_singleton_project_name(self):
        assert settings.PROJECT_NAME == "Autonomous NOC API"

    def test_settings_singleton_prometheus_url(self):
        assert settings.PROMETHEUS_URL == "http://prometheus:9090"

    def test_settings_singleton_keep_url(self):
        assert settings.KEEP_URL == "http://keep:8080"


class TestSettingsInnerConfigClass:
    def test_has_inner_config_class(self):
        """PR changed from ConfigDict to inner Config class."""
        assert hasattr(Settings, "Config") or hasattr(Settings, "model_config")

    def test_env_file_configured(self):
        """Settings should reference a .env file."""
        # pydantic v1 uses inner Config.env_file; pydantic v2 uses model_config
        if hasattr(Settings, "Config"):
            assert Settings.Config.env_file == ".env"
        elif hasattr(Settings, "model_config"):
            assert Settings.model_config.get("env_file") == ".env"


class TestSettingsOverrideViaEnv:
    def test_prometheus_url_can_be_overridden(self, monkeypatch):
        monkeypatch.setenv("PROMETHEUS_URL", "http://localhost:9090")
        s = Settings()
        assert s.PROMETHEUS_URL == "http://localhost:9090"

    def test_keep_url_can_be_overridden(self, monkeypatch):
        monkeypatch.setenv("KEEP_URL", "http://localhost:8080")
        s = Settings()
        assert s.KEEP_URL == "http://localhost:8080"

    def test_project_name_can_be_overridden(self, monkeypatch):
        monkeypatch.setenv("PROJECT_NAME", "Test NOC API")
        s = Settings()
        assert s.PROJECT_NAME == "Test NOC API"