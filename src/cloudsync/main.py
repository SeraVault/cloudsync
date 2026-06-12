import logging
import sys


def main() -> int:
    _configure_logging()
    args = _bootstrap_runtime(sys.argv[1:])
    _add_file_logging()

    if args.headless or args.sync_folder:
        return _headless_sync(sys.argv[1:])

    from .app import CloudSyncApp

    app = CloudSyncApp(background=args.background)
    return app.run(sys.argv)


def headless_main() -> int:
    """Dedicated headless entry point for cron, systemd, and server use."""
    _configure_logging()
    _bootstrap_runtime(sys.argv[1:])
    _add_file_logging()
    return _headless_sync(sys.argv[1:])


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _add_file_logging() -> None:
    """Attach a rotating file handler after DATA_DIR is finalised."""
    import logging.handlers
    from .core.config import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_path = DATA_DIR / "cloudsync.log"
    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,  # 2 MB per file
        backupCount=3,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logging.getLogger().addHandler(handler)


def _bootstrap_runtime(argv: list[str]):
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", metavar="FILE")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--sync-folder", action="append", metavar="PATH")
    args, _ = parser.parse_known_args(argv)

    if args.config:
        from .core.config import set_config_file

        set_config_file(args.config)

    return args


def _headless_sync(argv: list[str] | None = None) -> int:
    """Run one headless sync cycle using the current config file."""
    import argparse

    parser = argparse.ArgumentParser(prog="cloudsync-headless")
    parser.add_argument("--config", metavar="FILE", help="Path to config.json")
    parser.add_argument(
        "--sync-folder",
        action="append",
        metavar="PATH",
        help=(
            "Only sync the configured folder at PATH. "
            "May be passed multiple times."
        ),
    )
    args, _ = parser.parse_known_args(argv)

    from .core.config import Config

    config = Config.load()
    selected = list(config.sync_folders)

    if args.sync_folder:
        targets = set(args.sync_folder)
        selected = [
            folder
            for folder in config.sync_folders
            if folder.local_path in targets
        ]
        if not selected:
            logging.error(
                "--sync-folder: no configured folder matches %r",
                sorted(targets),
            )
            return 1

    folders = [folder for folder in selected if folder.enabled]
    if not folders:
        logging.info("No enabled folders selected; nothing to sync.")
        return 0

    overall_failed = False
    total_uploaded = 0
    total_downloaded = 0
    providers: list[str] = []

    for folder in folders:
        if folder.provider not in providers:
            providers.append(folder.provider)

    for provider in providers:
        provider_folders = [
            folder for folder in folders if folder.provider == provider
        ]
        try:
            client = _build_client(provider)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            logging.error(
                "Could not initialise %s provider: %s",
                provider,
                exc,
            )
            overall_failed = True
            continue

        from .sync.engine import SyncEngine

        engine = SyncEngine(config, client, provider_id=provider)
        result = engine.run_folders(provider_folders)
        total_uploaded += result.uploaded
        total_downloaded += result.downloaded

        if result.errors:
            overall_failed = True
            for err in result.errors:
                logging.error("Sync error (%s): %s", provider, err)

    if overall_failed:
        return 1

    logging.info(
        "Sync complete - up %d down %d across %d folder(s)",
        total_uploaded,
        total_downloaded,
        len(folders),
    )
    return 0


def _build_client(provider: str):
    """Instantiate a storage client for headless use."""
    if provider == "dropbox":
        from .core.dropbox_auth import DropboxAuth
        from .sync.dropbox import DropboxClient

        auth = DropboxAuth()
        if not auth.is_authenticated:
            raise RuntimeError(
                "Dropbox token not found. Run the app first to sign in."
            )
        return DropboxClient(auth)

    if provider == "s3":
        from .core.s3_auth import S3Auth
        from .sync.s3 import S3Client

        auth = S3Auth()
        if not auth.is_authenticated:
            raise RuntimeError(
                "S3 credentials not found. Run the app first to sign in."
            )
        return S3Client(auth)

    if provider == "onedrive":
        from .core.onedrive_auth import OneDriveAuth
        from .sync.onedrive import OneDriveClient

        auth = OneDriveAuth()
        if not auth.is_authenticated:
            raise RuntimeError(
                "OneDrive token not found. Run the app first to sign in."
            )
        return OneDriveClient(auth)

    from .core.auth import GoogleAuth
    from .sync.gdrive import DriveClient

    auth = GoogleAuth()
    if not auth.is_authenticated:
        raise RuntimeError(
            "Google credentials not found. Run the app first to sign in."
        )
    return DriveClient(auth)


if __name__ == "__main__":
    raise SystemExit(main())
