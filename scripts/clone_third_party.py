import subprocess
from pathlib import Path


REPOSITORIES = {
    "marketingskills": "https://github.com/javiertarazon/marketingskills.git",
    "hyperframes": "https://github.com/javiertarazon/hyperframes.git",
    "VoiceStudio": "https://github.com/javiertarazon/VoiceStudio.git",
    "Scrapegraph-ai": "https://github.com/javiertarazon/Scrapegraph-ai.git",
    "OpenMontage": "https://github.com/javiertarazon/OpenMontage.git",
    "Open-Generative-AI": "https://github.com/javiertarazon/Open-Generative-AI.git",
    "remotion": "https://github.com/javiertarazon/remotion.git",
}


def main() -> None:
    third_party = Path(__file__).resolve().parents[1] / "third_party"
    third_party.mkdir(parents=True, exist_ok=True)

    for name, url in REPOSITORIES.items():
        destination = third_party / name
        if destination.exists():
            if (destination / ".git").exists():
                print(f"Se omite el repositorio existente: {destination}")
                continue
            raise FileExistsError(
                f"El destino existe y no parece un clon Git; no se sobrescribe: {destination}"
            )
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--filter=blob:limit=10m",
                url,
                str(destination),
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
