"""Console entry point for the Windows distribution; no account access during checks."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if sys.argv[1:] == ["--version"]:
        from importlib.metadata import version
        print(f"Noneto CLI {version('noneto-cli')}")
        raise SystemExit(0)
    if sys.argv[1:] in (["--self-test"], ["--self-test-login"]):
        from bundle_checks import check
        raise SystemExit(check(login=sys.argv[1] == "--self-test-login"))
    if sys.argv[1:] == ["--self-test-mpv"]:
        from bundle_checks import check_player
        raise SystemExit(check_player())
    from noneto.app import main
    raise SystemExit(main())
