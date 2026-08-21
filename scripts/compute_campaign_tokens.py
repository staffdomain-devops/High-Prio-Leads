import json
import os
from datetime import date


def main():
    runner_temp = os.environ["RUNNER_TEMP"]
    out_path = os.path.join(runner_temp, "campaign_tokens.json")

    output = {
        "current_date": date.today().isoformat(),
    }

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"campaign_tokens.json written: current_date={output['current_date']}")


if __name__ == "__main__":
    main()
