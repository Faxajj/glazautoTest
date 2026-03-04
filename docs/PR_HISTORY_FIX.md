# PR не сравнивается с main (no common history)

Если GitHub показывает:

- `There isn’t anything to compare`
- `Branch has no history in common with trunk/main`

значит ветка создана из другой истории (обычно после `git init` в уже существующей папке).

## Быстрый фикс

```bash
git remote add origin https://github.com/<USERNAME>/<REPO>.git  # если не добавлен
git fetch origin

git checkout -b pr/fix-history origin/main
git cherry-pick <HASH_ВАШЕГО_КОММИТА>
git push -u origin pr/fix-history
```

После этого открывай PR из `pr/fix-history` в `main`.

## Проверка перед PR

```bash
python scripts/check_conflict_markers.py
```

Скрипт не даст закоммитить неразрешённые маркеры merge-конфликтов (`<<<<<<<`, `=======`, `>>>>>>>`).

## Почему в GitHub всё ещё видны `<<<<<<< ======= >>>>>>>`

Если вы открыли экран **Resolve conflicts** в PR, GitHub показывает конфликтные маркеры во встроенном редакторе.
Это **UI для разрешения merge-конфликта**, а не обязательно содержимое файлов в вашей ветке.

Проверьте локально:

```bash
python scripts/check_conflict_markers.py
```

Если скрипт пишет `OK: no conflict markers found.`, значит маркеров в коммитах нет — конфликт только на этапе слияния веток в PR.

