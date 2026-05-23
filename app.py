from core.state_store import initialize_state_backend


if __name__ == "__main__":
    backend = initialize_state_backend()
    print(f"state backend: {backend.get('backend')} {backend.get('reason')}")
    from ui.main_window import main

    main()
