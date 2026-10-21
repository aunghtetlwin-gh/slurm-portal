"""Run interactively on lai512 to initialize portal accounts."""
from getpass import getpass
from auth import add_user

for username, role in [('lailai','admin'), ('mangkud','user')]:
    password = getpass(f'New portal password for {username} ({role}): ')
    confirm = getpass('Confirm password: ')
    if password != confirm:
        raise SystemExit('Passwords did not match; stopped')
    add_user(username, password, role)
    print(f'{username} updated')

