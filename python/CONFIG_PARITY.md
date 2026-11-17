# Standalone configuration compatibility

`artex.config` represents the `Config` and `Database` structs in
`config/config.go`. Its loader reads only the selected JSON file and returns a
zero configuration when that file is missing, unreadable or invalid JSON. It
does not establish a database connection.

The Go loader calls `encoding/json.Unmarshal` into a struct and ignores the
returned error. The Python loader therefore preserves these member semantics:

- JSON tag matching ignores case, including the Unicode simple-fold aliases
  long s and Kelvin sign. It does not expand characters such as `ß` into `ss`.
- Members are processed in document order. Repeated `database` objects merge
  their fields, and later valid scalar values replace earlier ones.
- Null and incompatible values leave a previously accepted field intact.
  A port must use an integer JSON token representable by the native signed
  integer width; a boolean, float, quoted integer or overflow is ignored.
- Unknown members are ignored, but invalid JSON syntax anywhere in the
  document, including `NaN` or infinity constants, yields a zero configuration.

For example, `{"DATABASE":{"HOST":"db.test"},"database":{"port":6000}}`
retains both the host and port. `{"database":{"host":"kept","host":null}}`
retains `kept`. These follow the checked-in Go structs and the
[Go 1.26.3 JSON decoding contract](https://pkg.go.dev/encoding/json@go1.26.3#Unmarshal).

Resolution and serialization remain unchanged: a nonblank `ARTEX_PG_DSN` wins
over `database.dsn`, which wins over assembled fields. Only host, user or database
name makes the field-based configuration eligible; a password or port alone does
not introduce an implicit database. Assembly retains the existing host/port/TLS
defaults and escaping. Dataclass serialization retains the lowercase JSON field
names. No permission, endpoint, startup or connection policy is changed here.

Existing deliberate differences remain: source execution anchors artifacts to
the working directory; bare IPv6 hosts are bracketed; DSN masking hides whole
query/fragment values and fails closed for keyword or malformed inputs. This
change does not weaken those behaviors to imitate Go's less restrictive masking.
Invalid UTF-8 files still yield a zero configuration instead of replacing bytes
inside strings. Exhaustive malformed-Unicode and URI serialization parity is not
claimed.

`tests/test_config_parity.py` uses synthetic temporary files and an isolated
environment. Its DSN tests parse generated strings locally and its masking tests
use invented values. Together with `tests/test_config.py`, these are authored
contract tests, not Go differential, database, startup or browser acceptance tests:

```sh
PYTHONPATH=python python -m unittest discover -s python/tests -p 'test_config*.py' -v
```
