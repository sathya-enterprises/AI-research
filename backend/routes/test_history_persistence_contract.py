"""Source-level regression checks for the frontend conversation persistence contract."""
from pathlib import Path


def main() -> None:
    from routes._chat_source import read_chat_source
    src = read_chat_source(Path(__file__).resolve().parents[2])
    required = [
        "messagesRef",
        "activeIdRef",
        "conversationsRef",
        "beforeunload",
        "saveConversations(next)",
        "saveConversations(base)",
        "setMessages(cleanedMessages)",
    ]
    missing = [x for x in required if x not in src]
    assert not missing, f"missing history persistence contract markers: {missing}"
    print("PASS: frontend history persistence contract present")


if __name__ == "__main__":
    main()
