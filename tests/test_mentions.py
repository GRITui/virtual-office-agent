"""parse_mentions: single agent, @all expansion, de-dup, ordering,
non-agent tokens, and the @ requirement (mutation guard for MENTION_RE)."""


def test_single_agent(room_env):
    assert room_env.parse_mentions("hey @alpha") == ["alpha"]


def test_all_expansion(room_env):
    assert room_env.parse_mentions("@all") == ["alpha", "beta", "gamma"]
    assert room_env.parse_mentions("@everyone") == ["alpha", "beta", "gamma"]


def test_dedup(room_env):
    assert room_env.parse_mentions("@alpha @alpha") == ["alpha"]
    # @all expands, then the explicit @alpha must not duplicate.
    assert room_env.parse_mentions("@all @alpha") == ["alpha", "beta", "gamma"]


def test_non_agent_tokens_ignored(room_env):
    assert room_env.parse_mentions("@nobody @ghost") == []
    assert room_env.parse_mentions("no mentions here") == []


def test_ordering(room_env):
    assert room_env.parse_mentions("@beta @alpha") == ["beta", "alpha"]


def test_mixed_order(room_env):
    # Explicit mention first, then @all fills in the rest in agent order.
    assert room_env.parse_mentions("@beta @all") == ["beta", "alpha", "gamma"]


def test_case_insensitive(room_env):
    assert room_env.parse_mentions("@ALPHA") == ["alpha"]


def test_at_required(room_env):
    """Mutation guard: dropping the @ from MENTION_RE makes this fail
    (bare agent names in prose would start rounds)."""
    assert room_env.parse_mentions("alpha beta gamma") == []
