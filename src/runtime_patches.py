import json
from urllib.request import Request, urlopen


def apply_runtime_patches(session):
    if getattr(session, "_codex_bridge_runtime_patches_applied", False):
        return
    session._codex_bridge_runtime_patches_applied = True
    _patch_blastprotein_pdbinfo()


def _patch_blastprotein_pdbinfo():
    try:
        from chimerax.blastprotein.data_model import pdbinfo
    except Exception:
        return

    if getattr(pdbinfo, "_codex_bridge_patched", False):
        return

    original = getattr(pdbinfo, "fetch_pdb_info", None)
    if original is None:
        return

    def fetch_pdb_info_json(entry_chain_list):
        query = pdbinfo.query_template % ",".join(
            ['"%s"' % entry_chain.split("_")[0] for entry_chain in entry_chain_list]
        )
        payload = json.dumps({"query": query}).encode("utf-8")
        req = Request(
            "https://data.rcsb.org/graphql",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urlopen(req) as f:
            info = json.loads(f.read().decode("utf-8"))
        if "errors" in info:
            raise ValueError("Fetching BLAST PDB info had errors: %s" % info["errors"])

        by_entry = {}
        for entry_data in info["data"]["entries"]:
            by_entry[entry_data["rcsb_id"]] = entry_data

        pdb_info = {}
        for info_key in entry_chain_list:
            entry, chain = info_key.split("_")
            pdb_info[info_key] = hits = {}
            if entry not in by_entry:
                continue
            for attr_name, mmcif_keys in pdbinfo.entry_attr_name_mapping:
                hits[attr_name] = pdbinfo.get_val(by_entry[entry], mmcif_keys)
            all_polys = by_entry[entry]["polymer_entities"]
            for poly in all_polys:
                try:
                    ids = poly["rcsb_polymer_entity_container_identifiers"]
                except KeyError:
                    continue
                if ids is None:
                    continue
                try:
                    auth_ids = ids["auth_asym_ids"]
                except KeyError:
                    continue
                if auth_ids and chain in auth_ids:
                    break
            else:
                poly = None
            for attr_name, per_chain, mmcif_keys in pdbinfo.chain_attr_name_mapping:
                if per_chain:
                    val = None if poly is None else pdbinfo.get_val(poly, mmcif_keys)
                else:
                    if all_polys is None:
                        val = None
                    elif len(mmcif_keys) == 1 and callable(mmcif_keys[0]):
                        val = mmcif_keys[0](all_polys)
                    else:
                        val = []
                        for p in all_polys:
                            pval = pdbinfo.get_val(p, mmcif_keys)
                            auth_ids = pdbinfo.get_val(
                                p,
                                [
                                    "rcsb_polymer_entity_container_identifiers",
                                    "auth_asym_ids",
                                ],
                            )
                            if auth_ids and chain in auth_ids:
                                if isinstance(pval, list):
                                    val.extend(pval)
                                elif pval is not None:
                                    val.append(pval)
                        if len(val) == 0:
                            val = None
                hits[attr_name] = val
        return pdb_info

    pdbinfo.fetch_pdb_info = fetch_pdb_info_json
    pdbinfo._codex_bridge_patched = True

