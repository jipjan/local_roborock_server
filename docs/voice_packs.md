# Voice packs

A robot changes its voice when an app sends `dnld_install_sound` with a URL to
download the pack from. The official Roborock app takes that URL from
Roborock's own list, so the robot is told to fetch the pack from Roborock's
servers. A robot without internet access, as is usual with this server, cannot
reach them and the voice change fails.

The server keeps its own copy of the list and the packs and serves them from
the stack, so a client can give the robot a URL on this server instead.

## How the mirror works

`GET https://<stack_fqdn>/voice-pkg/<bucket>/info` returns Roborock's list for
a product, with every pack, picture and preview URL rewritten to point at this
server. A non-standard advertised HTTPS port is included in those URLs.

`GET https://<stack_fqdn>/voice-pkg/<bucket>/{package,pic,wav}/<file>` serves
one file from that list. The first request fetches it from Roborock, checks it
against the MD5 that Roborock's storage sends as its ETag, and keeps it under
`<data_dir>/voice_packs/<bucket>/`. Later requests are served from that copy.
A package is fetched again when the list shows a new MD5 or version for it.

The first time a bucket's list is requested, the server downloads every pack in
it in the background, so later voice changes need no internet access. The list
is refreshed from Roborock **at most once a day**. When Roborock cannot be
reached, the last stored list is used and the refresh is retried after
**10 minutes**.

The upstream host follows `network.region` (`eu`, `us` or `cn`). Other regions
use `eu`, and a warning is logged.

## Limits

The routes need no login: the robot downloads without credentials, and
Roborock's own copies are public too. To keep the routes from being used to make
the server fetch arbitrary files:

- Bucket names and file names are validated, and only files named in a stored
  list are fetched.
- At most **8 buckets** are mirrored.
- A file larger than **64 MiB**, or a list larger than **2 MiB**, is refused.
- A file Roborock refuses is not requested again for **10 minutes**.

## Buckets

Roborock keeps each product's voice packs in a folder named after the product's
code name in its device plugin:

| Bucket | Models |
|---|---|
| `vida` | Saros 20 Neo (`roborock.vacuum.a295`, `roborock.vacuum.a296`) |

Other products' buckets are in their device plugin, as the `bucket` value used
by the voice-pack page. Add a row here once one is confirmed.

## Clients

The official Roborock app reads the list from Roborock directly. It still sends
Roborock's URLs, so voice changes from the official app keep failing on a robot
without internet access.

A client that uses the mirror reads `/voice-pkg/<bucket>/info`, takes one
`voice_list` entry and sends the robot its values:

```json
{
  "method": "dnld_install_sound",
  "params": {
    "sid": 129,
    "url": "https://api-vac.cc/voice-pkg/vida/package/nl.pkg",
    "md5": "<voice_pkg_md5 from the list>",
    "sver": 4
  }
}
```

`sid` is `voice_id`, `url` is `voice_pkg_url`, `md5` is `voice_pkg_md5` and
`sver` is `version`. Pass `voice_pkg_md5` exactly as listed: it is not the MD5
of the file. Follow the installation with `get_sound_progress`.

## Home Assistant

Set this option in the add-on configuration, then restart it:

```yaml
voice_packs_mode: mirror
```

The values are the same as for `config.toml` below.

## Docker and standalone installs

Set the mode in `config.toml`:

```toml
[voice_packs]
mode = "mirror"   # mirror | offline | disabled
```

- `mirror` (the default) fetches from Roborock on first use and keeps copies.
- `offline` serves only the copies already stored and never contacts Roborock.
  Use it after the packs have been mirrored once, or after copying a
  `voice_packs` folder into the data directory.
- `disabled` turns the `/voice-pkg` routes off; they answer 404.
