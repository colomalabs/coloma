from backend import config


def test_deployment_settings_default_to_local_proxy_values(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")

    app_config = config.read_app_config()

    assert app_config.deployment.port == 8000
    assert app_config.deployment.api_key == "EMPTY"


def test_deployment_settings_are_normalized_and_persisted(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    app_config = config.AppConfig(
        deployment=config.DeploymentConfig(port=70_000, api_key="  deploy-key  ")
    )

    saved = config.write_app_config(app_config)
    loaded = config.read_app_config()

    assert saved.deployment.port == 65_535
    assert loaded.deployment.port == 65_535
    assert loaded.deployment.api_key == "deploy-key"
