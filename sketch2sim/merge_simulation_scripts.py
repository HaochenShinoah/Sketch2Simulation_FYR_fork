from pathlib import Path


def merge_basis_and_configuration(
    basis_path: str,
    configuration_path: str,
    output_path: str,
) -> None:
    basis_file = Path(basis_path)
    configuration_file = Path(configuration_path)

    if not basis_file.exists():
        raise FileNotFoundError(f"Basis file not found: {basis_file}")
    if not configuration_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {configuration_file}")

    final_code = configuration_file.read_text(encoding="utf-8")
    Path(output_path).write_text(final_code, encoding="utf-8")

    print(f"Merged script written to: {Path(output_path).resolve()}")
