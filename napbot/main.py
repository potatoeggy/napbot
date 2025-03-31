import asyncio

from .bot import run_bot


def main():
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
