from auth import issue_token
from auth import LAB_SECRET


def _panel_token() -> str:
    """Emite um token para o painel, como um cliente HTTP faria."""
    result = issue_token("panel", "manifesty1-lab")
    assert result, "issue_token falhou — segredo do lab mudou?"
    token, _ = result
    return token


# onde antes era:
#   headers={"Authorization": f"Bearer {API_TOKEN}"}
# passa a ser:
#   headers={"Authorization": f"Bearer {_panel_token()}"}
