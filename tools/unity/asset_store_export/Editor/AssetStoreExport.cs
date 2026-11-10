// Exports the prefabs of Unity Asset Store packages the user downloaded, as OBJ + MTL + albedo PNG, for
// `onward-generator library asset-store import`. Runs in a throwaway project (never the Onward project), in batch mode:
//
//   Unity -batchmode -nographics -projectPath <tmp> -importPackage <pack.unitypackage> -executeMethod AssetStoreExport.Run
//         -onwardId <id> -onwardOut <dir> -onwardMaxTriangles 8000 -quit
//
// One launch per package: `-importPackage` imports synchronously before the method runs (AssetDatabase.ImportPackage
// is asynchronous and would export nothing). The method exports every prefab with a mesh (LOD0 only), writes
// <out>/<id>/report.json, and deletes everything under Assets/ but Assets/Editor, so the project stays small. Nothing here edits a package file. Coordinates: Unity is left-handed Y up; OBJ is written
// right-handed Y up (x negated, winding flipped), in metres as the prefab places them.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEngine;

public static class AssetStoreExport
{
    const int TexturePx = 512;
    static readonly string[] AlbedoNames = { "_BaseMap", "_BaseColorMap", "_MainTex", "_Albedo", "_AlbedoMap", "_Diffuse" };
    static readonly string[] ColourNames = { "_BaseColor", "_Color", "_AlbedoColor" };

    [Serializable] class Item { public string prefab; public string file; public int triangles; public float[] size; public string status; public string[] materials; }
    [Serializable] class Report { public string id; public string package; public string status; public string error; public List<Item> items = new List<Item>(); }

    public static void Run()
    {
        var args = Environment.GetCommandLineArgs();
        string Arg(string name, string fallback = null) { int i = Array.IndexOf(args, name); return i >= 0 && i + 1 < args.Length ? args[i + 1] : fallback; }
        string id = Arg("-onwardId"), outDir = Arg("-onwardOut"), package = Arg("-importPackage", "");
        int maxTriangles = int.Parse(Arg("-onwardMaxTriangles", "8000"), CultureInfo.InvariantCulture);
        if (id == null || outDir == null) { Debug.LogError("[AssetStoreExport] -onwardId and -onwardOut are required"); EditorApplication.Exit(2); return; }
        ExportPackage(id, package, Path.Combine(outDir, id), maxTriangles);
        EditorApplication.Exit(0);
    }

    static void ExportPackage(string id, string package, string target, int maxTriangles)
    {
        var report = new Report { id = id, package = package, status = "ok" };
        Directory.CreateDirectory(target);
        try
        {
            var added = AssetDatabase.GetAllAssetPaths()
                .Where(p => p.StartsWith("Assets/") && !p.StartsWith("Assets/Editor")).ToList();
            foreach (var path in added.Where(p => p.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase)).OrderBy(p => p, StringComparer.Ordinal))
                report.items.Add(ExportPrefab(path, target, maxTriangles));
            // Prefabs a 2021 editor cannot read (Unity 3.x binary prefabs) give no mesh: export the models instead,
            // one item per top-level child, since such packs often hold every prop in one FBX.
            if (report.items.All(i => i.status != "exported"))
                foreach (var path in added.Where(p => p.EndsWith(".fbx", StringComparison.OrdinalIgnoreCase)).OrderBy(p => p, StringComparer.Ordinal))
                    report.items.AddRange(ExportModel(path, target, maxTriangles));
            Cleanup(added);
        }
        catch (Exception e)
        {
            report.status = "failed";
            report.error = e.GetType().Name + ": " + e.Message;
        }
        File.WriteAllText(Path.Combine(target, "report.json"), JsonUtility.ToJson(report, true));
        Debug.Log($"[AssetStoreExport] {id}: {report.status}, {report.items.Count(i => i.status == "exported")} exported of {report.items.Count}");
    }

    static void Cleanup(List<string> added)
    {
        foreach (var root in added.Select(p => string.Join("/", p.Split('/').Take(2))).Distinct().Where(r => r != "Assets"))
            AssetDatabase.DeleteAsset(root);
        AssetDatabase.Refresh();
    }

    static Item ExportPrefab(string path, string target, int maxTriangles)
    {
        var asset = AssetDatabase.LoadAssetAtPath<GameObject>(path);
        if (asset == null) return new Item { prefab = path, status = "unreadable" };
        var go = (GameObject)UnityEngine.Object.Instantiate(asset);
        try { return ExportObject(go, path, Safe(Path.GetFileNameWithoutExtension(path)), target, maxTriangles); }
        finally { UnityEngine.Object.DestroyImmediate(go); }
    }

    static List<Item> ExportModel(string path, string target, int maxTriangles)
    {
        var asset = AssetDatabase.LoadAssetAtPath<GameObject>(path);
        if (asset == null) return new List<Item> { new Item { prefab = path, status = "unreadable" } };
        var go = (GameObject)UnityEngine.Object.Instantiate(asset);
        try
        {
            string stem = Safe(Path.GetFileNameWithoutExtension(path));
            var parts = Enumerable.Range(0, go.transform.childCount).Select(i => go.transform.GetChild(i).gameObject)
                .Where(c => Lod0(c).Count > 0).ToList();
            if (parts.Count <= 1) return new List<Item> { ExportObject(go, path, stem, target, maxTriangles) };
            return parts.Select(c => ExportObject(c, path + "#" + c.name, stem + "_" + Safe(c.name), target, maxTriangles)).ToList();
        }
        finally { UnityEngine.Object.DestroyImmediate(go); }
    }

    // The object's meshes in its root frame, moved so the footprint is centred on the origin and the lowest point is at 0.
    static Item ExportObject(GameObject go, string source, string stem, string target, int maxTriangles)
    {
        var item = new Item { prefab = source, status = "exported" };
        try
        {
            // Keep the authored rotation and scale (an FBX child often carries the axis fix of its exporter);
            // the shift below centres the footprint and puts the lowest point at 0.
            go.transform.SetParent(null, true);
            var renderers = Lod0(go);
            if (renderers.Count == 0) { item.status = "no_mesh"; return item; }
            int triangles = renderers.Sum(r => r.GetComponent<MeshFilter>().sharedMesh.triangles.Length / 3);
            item.triangles = triangles;
            if (triangles > maxTriangles) { item.status = "too_many_triangles"; return item; }
            if (renderers.SelectMany(r => r.sharedMaterials).Any(IsTransparent)) { item.status = "transparent_material"; return item; }
            var bounds = renderers.Select(r => r.bounds).Aggregate((a, b) => { a.Encapsulate(b); return a; });
            item.size = new[] { bounds.size.x, bounds.size.z, bounds.size.y };
            var shift = new Vector3(-bounds.center.x, -bounds.min.y, -bounds.center.z);
            // Two prefabs with one name in different folders (variants) must not overwrite each other.
            string unique = stem;
            for (int k = 2; File.Exists(Path.Combine(target, unique + ".obj")); k++) unique = stem + "_" + k;
            stem = unique;
            item.file = stem + ".obj";
            item.materials = WriteObj(renderers, target, stem, shift);
        }
        catch (Exception e)
        {
            item.status = "failed: " + e.Message;
        }
        return item;
    }

    static List<MeshRenderer> Lod0(GameObject go)
    {
        var skipped = new HashSet<Renderer>();
        foreach (var group in go.GetComponentsInChildren<LODGroup>(true))
        {
            var lods = group.GetLODs();
            for (int i = 1; i < lods.Length; i++) foreach (var r in lods[i].renderers) if (r != null) skipped.Add(r);
        }
        return go.GetComponentsInChildren<MeshRenderer>(false)
            .Where(r => !skipped.Contains(r) && r.enabled && r.GetComponent<MeshFilter>() != null && r.GetComponent<MeshFilter>().sharedMesh != null)
            .ToList();
    }

    static bool IsTransparent(Material m)
    {
        if (m == null) return false;
        if (m.renderQueue >= 2450) return true;
        if (m.HasProperty("_Surface") && m.GetFloat("_Surface") > 0.5f) return true;
        if (m.HasProperty("_AlphaClip") && m.GetFloat("_AlphaClip") > 0.5f) return true;
        return m.IsKeywordEnabled("_ALPHATEST_ON") || m.IsKeywordEnabled("_ALPHABLEND_ON") || m.IsKeywordEnabled("_ALPHAPREMULTIPLY_ON");
    }

    static string[] WriteObj(List<MeshRenderer> renderers, string target, string stem, Vector3 shift)
    {
        var obj = new StringBuilder("# exported by tools/unity/asset_store_export\nmtllib " + stem + ".mtl\n");
        var mtl = new StringBuilder();
        var written = new Dictionary<Material, string>();
        int vBase = 1;
        foreach (var r in renderers)
        {
            var mesh = r.GetComponent<MeshFilter>().sharedMesh;
            var m = r.transform.localToWorldMatrix;
            var vs = mesh.vertices; var ns = mesh.normals; var uv = mesh.uv;
            var nm = m.inverse.transpose;
            foreach (var v in vs) { var w = m.MultiplyPoint3x4(v) + shift; obj.AppendFormat(CultureInfo.InvariantCulture, "v {0} {1} {2}\n", -w.x, w.y, w.z); }
            for (int i = 0; i < vs.Length; i++)
            {
                var n = ns.Length == vs.Length ? nm.MultiplyVector(ns[i]).normalized : Vector3.up;
                obj.AppendFormat(CultureInfo.InvariantCulture, "vn {0} {1} {2}\n", -n.x, n.y, n.z);
                var t = uv.Length == vs.Length ? uv[i] : Vector2.zero;
                obj.AppendFormat(CultureInfo.InvariantCulture, "vt {0} {1}\n", t.x, t.y);
            }
            var mats = r.sharedMaterials;
            for (int s = 0; s < mesh.subMeshCount; s++)
            {
                var mat = mats.Length > 0 ? mats[Math.Min(s, mats.Length - 1)] : null;
                string name = MaterialName(mat, written, mtl, target, stem);
                obj.Append("usemtl ").Append(name).Append('\n');
                var tris = mesh.GetTriangles(s);
                for (int i = 0; i < tris.Length; i += 3)
                {
                    int a = tris[i] + vBase, b = tris[i + 2] + vBase, c = tris[i + 1] + vBase;  // flipped for the x negation
                    obj.AppendFormat("f {0}/{0}/{0} {1}/{1}/{1} {2}/{2}/{2}\n", a, b, c);
                }
            }
            vBase += vs.Length;
        }
        File.WriteAllText(Path.Combine(target, stem + ".obj"), obj.ToString());
        File.WriteAllText(Path.Combine(target, stem + ".mtl"), mtl.ToString());
        return written.Values.ToArray();
    }

    static string MaterialName(Material mat, Dictionary<Material, string> written, StringBuilder mtl, string target, string stem)
    {
        if (mat == null) mat = new Material(Shader.Find("Standard")) { name = "default" };
        if (written.TryGetValue(mat, out var known)) return known;
        string name = Safe(stem + "_" + mat.name);
        written[mat] = name;
        Color colour = Color.white;
        foreach (var c in ColourNames) if (mat.HasProperty(c)) { colour = mat.GetColor(c); break; }
        Texture tex = null; Vector2 scale = Vector2.one, offset = Vector2.zero;
        foreach (var t in AlbedoNames)
            if (mat.HasProperty(t) && mat.GetTexture(t) != null) { tex = mat.GetTexture(t); scale = mat.GetTextureScale(t); offset = mat.GetTextureOffset(t); break; }
        var lin = colour.linear;
        mtl.AppendFormat(CultureInfo.InvariantCulture, "newmtl {0}\nKd {1} {2} {3}\n", name, lin.r, lin.g, lin.b);
        if (tex != null)
        {
            string png = name + ".png";
            File.WriteAllBytes(Path.Combine(target, png), Readable(tex, colour));
            mtl.AppendFormat(CultureInfo.InvariantCulture, "map_Kd -s {0} {1} 1 -o {2} {3} 0 {4}\n", scale.x, scale.y, offset.x, offset.y, png);
        }
        return name;
    }

    // Any texture, readable or not, compressed or not: blit it at most TexturePx wide and read it back, tinted by the
    // material colour (the albedo the shader would show: _BaseColor x _BaseMap).
    static byte[] Readable(Texture tex, Color tint)
    {
        float k = Mathf.Min(1f, TexturePx / (float)Mathf.Max(tex.width, tex.height));
        int w = Mathf.Max(1, Mathf.RoundToInt(tex.width * k)), h = Mathf.Max(1, Mathf.RoundToInt(tex.height * k));
        var rt = RenderTexture.GetTemporary(w, h, 0, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB);
        Graphics.Blit(tex, rt);
        var previous = RenderTexture.active;
        RenderTexture.active = rt;
        var copy = new Texture2D(w, h, TextureFormat.RGB24, false);
        copy.ReadPixels(new Rect(0, 0, w, h), 0, 0);
        RenderTexture.active = previous;
        RenderTexture.ReleaseTemporary(rt);
        var px = copy.GetPixels();
        for (int i = 0; i < px.Length; i++) px[i] = new Color(px[i].r * tint.r, px[i].g * tint.g, px[i].b * tint.b, 1f);
        copy.SetPixels(px);
        var bytes = copy.EncodeToPNG();
        UnityEngine.Object.DestroyImmediate(copy);
        return bytes;
    }

    static string Safe(string s) => new string(s.Select(ch => char.IsLetterOrDigit(ch) ? ch : '_').ToArray()).Trim('_');
}
