#!/usr/bin/env python3
"""Navigate to Skool /chat inbox page for sync so chat list is visible."""
APP = "backend/app.py"


def main():
    with open(APP, "r") as f:
        c = f.read()

    old = """    attempts = [
        "https://www.skool.com/",
    ]"""
    new = """    attempts = [
        "https://www.skool.com/chat",
        "https://www.skool.com/",
    ]"""
    if new not in c:
        c = c.replace(old, new)
        print("Added /chat as first nav attempt")
    with open(APP, "w") as f:
        f.write(c)
    print("Patch applied")


if __name__ == "__main__":
    main()
