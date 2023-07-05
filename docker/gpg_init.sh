#!/bin/bash
set -xe
gpg --batch --gen-key <<'EOF'
  Key-Type: RSA
  Key-Length: 1024
  Name-Real: Unrecoverable
  Name-Email: unrecoverable@example.com
  Expire-Date: 0
  Passphrase: test
  %commit
EOF
echo "goodbye cruel world" | gpg --batch --encrypt -a -r unrecoverable@example.com > /tmp/unreadable.gpg
gpg --batch --yes --delete-secret-keys $(gpg --fingerprint --with-colons | sed '/^fpr/!d;s,:$,,;s,.*:,,')
gpg --batch --gen-key <<'EOF'
  Key-Type: RSA
  Key-Length: 1024
  Name-Real: Tester
  Name-Email: test@example.com
  Expire-Date: 0
  Passphrase: test
  %commit
EOF
pass init test@example.com
printf "hello world" | pass insert -mf test
mv /tmp/unreadable.gpg /root/.password-store/
