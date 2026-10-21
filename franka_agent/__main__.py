"""Talk to the arm without a GPU, against the mock backend.

    python -m franka_agent "put the red cube on the blue one"
    python -m franka_agent            # interactive prompt
    python -m franka_agent --lang ja "赤いキューブを青いキューブの上に置いて"
"""

import argparse

from .agent import DEFAULT_PROVIDER, PROVIDERS, LanguageAgent
from .backend import MockBackend
from .translate import LANGUAGES, Translator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="*")
    parser.add_argument("--drop-rate", type=float, default=0.0, help="fraction of grasps that slip")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default=DEFAULT_PROVIDER)
    parser.add_argument("--model", default=None, help="override the provider's default model")
    parser.add_argument("--effort", default="low", help="reasoning effort: none, low, medium, high")
    parser.add_argument("--lang", default=None, choices=sorted(LANGUAGES),
                        help="talk to the arm in this language (NVIDIA Riva Translate)")
    args = parser.parse_args()

    agent = LanguageAgent(MockBackend(drop_rate=args.drop_rate), provider=args.provider,
                          model=args.model, effort=args.effort,
                          translator=Translator(args.lang) if args.lang else None)
    if args.command:
        agent.run(" ".join(args.command))
        return
    while True:
        try:
            line = input("\ncommand> ").strip()
        except EOFError:
            break
        if line:
            agent.run(line)


if __name__ == "__main__":
    main()
