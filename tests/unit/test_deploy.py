from pathlib import Path

ROOT = Path(__file__).parent.parent.parent


def test_chart_nginx_conf_matches_image() -> None:
    """Чарт монтирует свою копию nginx.conf — она не должна расходиться с образом."""
    image = (ROOT / "deploy/docker/nginx.conf").read_text()
    chart = (ROOT / "deploy/helm/gosdoc/files/nginx.conf").read_text()
    assert chart == image
