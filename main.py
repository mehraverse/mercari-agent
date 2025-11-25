"""Simple CLI entry point for the Mercari shopping agent."""

import asyncio

from mercari_agent import MercariChatAgent

async def main() -> None:
    agent = MercariChatAgent()
    print("Mercari assistant is ready. Type 'exit' or 'quit' to stop.")

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "clear"}:
            print("Goodbye.")
            break

        reply = await agent.chat(user_input)
        print(f"Agent: {reply}\n")

    print("Session ended.")


if __name__ == "__main__":
    asyncio.run(main())
