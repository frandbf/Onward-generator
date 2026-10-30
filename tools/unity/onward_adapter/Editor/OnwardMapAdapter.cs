#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using DPI.Navigation;
using Onward.CustomContent;
using Onward.CustomContent.Assets;
using Onward.CustomContent.EditorTools;
using Onward.CustomMaps;
using Onward.GameVariants;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.AI;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using Debug = UnityEngine.Debug;

namespace OnwardGenerator
{
    // Phase 07 Onward export adapter (onward_unity 0.1.0). Its only input is an export
    // package written by `onward-generator export package`: every file is checked against
    // the manifest SHA-256 before anything is built. The scene is assembled with the SDK
    // v1.15 prefabs and built with the SDK's Onward Launcher, exactly as the phase 05
    // harness (MurciaPerfBuilder) that was loaded and measured on a Quest 3.
    public static class OnwardMapAdapter
    {
        public const string AdapterName = "onward_unity";
        public const string AdapterVersion = "0.1.0";
        private const string PackageSchema = "onward_generator.onward_export_package";
        private const string PackageSchemaVersion = "0.1.0";

        // Belluga packages written before the rename still use murcia_generator.<kind>.
        private static bool IsSchema(string value, string kind)
        {
            return value == "onward_generator." + kind || value == "murcia_generator." + kind;
        }
        private const string LogPrefix = "[OnwardAdapter]";
        // LookRotation rejects a zero vector. A snapped waypoint pair closer than
        // this is the same point, and Hunt's LateUpdate then overflows the stack.
        private const float MinLookDirectionM = 0.25f;
        // The layout carries 32 Hunt riflemen. Older packages closed from the
        // fourth agent. Murcia's package kept 24 and the match stayed open.
        private const int StableHuntBots = 32;
        // The Quest main thread dies around 500 recursive frames. A deeper
        // neighbor walk closes the game a few seconds after Hunt spawns.
        private const int MaxNavWalkDepth = 400;
        private const int NavNodeBudget = 320;
        private const int NavSpacingAttempts = 6;
        private const string SdkRoot = "Assets/OnwardCustomContent";
        private const string PrefabRoot = SdkRoot + "/Prefabs";
        private const string LitShaderPath = SdkRoot + "/Internal/Shaders/PP_Opaque_Simple_Lit.shadergraph";
        // PP_Sprite_Lit is already transparent, Render Face Both, no alpha clip.
        // It has no _BaseColor. Alpha is vertex alpha times _MainTex alpha.
        private const string SpriteShaderPath = SdkRoot + "/Internal/Shaders/PP_Sprite_Lit.shadergraph";
        private const string ViewGlassMaterial = "glass_view";
        private const string WorkRoot = "Assets/OnwardAdapter";
        private const string LauncherBinPathPref = "_ONWARD_BIN_PATH_";
        private const string LauncherPlatformPref = "_ONWARD_PLATFORM_";
        private const int LauncherPlatformQuest = 1;
        private const float SpawnHeight = 0.05f;

        // ---- Package ----------------------------------------------------------------------
        [Serializable] private class PAdapter { public string name; public string version; }
        [Serializable] private class PTarget { public string unity; public string sdk; public string platform; }
        [Serializable] private class PMap { public string id; public string title; public string description; }
        [Serializable] private class PFile { public string role; public string path; public string sha256; }

        [Serializable]
        private class Manifest
        {
            public string schema;
            public string schema_version;
            public string package_id;
            public PAdapter adapter;
            public PTarget target;
            public PMap map;
            public PFile[] files;
        }

        [Serializable]
        private class Options
        {
            public string variant = "";
            public bool static_batching = false;
            public bool occlusion_culling = false;
            public float lightmap_texels_per_m = 10f;
            public int lightmap_max_size = 2048;
            public bool lightmap_directional = false;
            // scaleInLightmap per glTF material (onward_generator.export.lightmap): roofs, ground and
            // interiors below 1. A material not listed keeps 1. Absent in older packages.
            public LightmapScale[] lightmap_scales = new LightmapScale[0];
            public float combine_cell_m = 0f;
            // ADR-013: baked point lights from the interior light anchors, at most this many per map.
            public int interior_lights_max = 24;
            public float interior_light_intensity = 0.8f;
            // ADR-014: linear RenderSettings fog. Off when the key is absent (older configurations).
            public bool fog_enabled = false;
            public float fog_start_m = 160f;
            public float fog_end_m = 1000f;
            public string fog_color = "#C8CDD2";
        }

        [Serializable] private class LightmapScale { public string material; public float scale = 1f; }
        [Serializable] private class XY { public float x; public float y; public float yaw; }
        [Serializable] private class Block { public float[] centre_m; public XY[] points_m; }
        [Serializable] private class Damage { public float z_m; public float[] size_m; public float[] centre_m; }
        [Serializable] private class Route { public XY[] points; }

        [Serializable]
        private class Layout
        {
            public string schema;
            public string schema_version;
            public Block attackers;
            public Block defenders;
            public Block attackers_b;
            public Block defenders_b;
            public float[] escort_a_m;
            public float[] escort_b_m;
            public float[] evac_m;
            public XY[] harddrives_m;
            public float[] uplink_m;
            public float[] map_quad_m;
            public float level_size_m;
            public float tent_z_m;
            public Damage damage_volume;
            public float[] focus_m;
            public XY[] ai_m;
            public Route[] routes;
            public XY[] ammo_m;
            public string audio_global;
            public float[] audio_birds_m;
            public float[] audio_wind_m;
            public string environment;
            public string time_of_day;
            public string profile;
            public float far_clip_m;
            public int flyover;
        }

        private sealed class Package
        {
            public string Dir;
            public Manifest Manifest;
            public string ManifestSha256;
            public Options Options;
            public Layout Layout;
            public string Path(string role) => System.IO.Path.Combine(Dir, Manifest.files.Single(f => f.role == role).path);
        }

        private static string Sha256(string path)
        {
            using (SHA256 sha = SHA256.Create())
            using (FileStream f = File.OpenRead(path))
            {
                return string.Concat(sha.ComputeHash(f).Select(b => b.ToString("x2")));
            }
        }

        private static Package LoadPackage(string dir)
        {
            string manifestPath = Path.Combine(dir, "onward_export.json");
            var m = JsonUtility.FromJson<Manifest>(File.ReadAllText(manifestPath));
            if (!IsSchema(m.schema, "onward_export_package") || m.schema_version != PackageSchemaVersion)
                throw new InvalidDataException($"{manifestPath}: not a {PackageSchema} {PackageSchemaVersion} manifest");
            if (m.adapter == null || m.adapter.name != AdapterName || m.adapter.version != AdapterVersion)
                throw new InvalidDataException($"package targets adapter {m.adapter?.name} {m.adapter?.version}; this is {AdapterName} {AdapterVersion}");
            if (m.target == null || m.target.unity != Application.unityVersion || m.target.platform != "quest")
                throw new InvalidDataException($"package targets Unity {m.target?.unity} ({m.target?.platform}); running {Application.unityVersion}");
            if (string.IsNullOrEmpty(m.package_id) || m.package_id.Any(ch => !(char.IsLetterOrDigit(ch) || ch == '_')))
                throw new InvalidDataException("package_id must be a plain identifier");
            foreach (string role in new[] { "visual", "collision", "layout", "options" })
            {
                if (m.files.Count(f => f.role == role) != 1)
                    throw new InvalidDataException($"package must list exactly one '{role}' file");
            }

            foreach (PFile f in m.files)
            {
                if (Path.GetFileName(f.path) != f.path)
                    throw new InvalidDataException($"{f.path}: package paths must be plain file names");
                string actual = Sha256(Path.Combine(dir, f.path));
                if (actual != f.sha256)
                    throw new InvalidDataException($"{f.path}: sha256 {actual} differs from the manifest ({f.sha256}); refusing to build");
            }

            var pkg = new Package { Dir = dir, Manifest = m, ManifestSha256 = Sha256(manifestPath) };
            pkg.Options = JsonUtility.FromJson<Options>(File.ReadAllText(pkg.Path("options")));
            pkg.Layout = JsonUtility.FromJson<Layout>(File.ReadAllText(pkg.Path("layout")));
            if (pkg.Options.variant != m.package_id)
                throw new InvalidDataException("adapter_options.json variant does not match package_id");
            if (!IsSchema(pkg.Layout.schema, "perf_layout") && !IsSchema(pkg.Layout.schema, "gameplay_layout"))
                throw new InvalidDataException("layout is not a onward_generator.perf_layout or onward_generator.gameplay_layout document");
            Debug.Log($"{LogPrefix} Package {m.package_id}: {m.files.Length} files verified (manifest sha256 {pkg.ManifestSha256})");
            return pkg;
        }

        // ---- glTF subset (same reader as the phase 05 harness) -------------------------
        [Serializable] private class GAttributes { public int POSITION = -1; public int NORMAL = -1; public int TEXCOORD_0 = -1; }
        [Serializable] private class GPrimitive { public GAttributes attributes; public int indices = -1; public int material = -1; public int mode = 4; }
        [Serializable] private class GMesh { public string name; public GPrimitive[] primitives; }
        // Node extras the adapter reads. JsonUtility skips every other key (uv_frame, source_ids, ...).
        // light_anchors is ADR-013: local metres (x east, y north, z up); probes_m is flat x, y, z triples.
        [Serializable] private class GRoomAnchor { public string room_id; public float[] light_m; public float range_m; public float[] probes_m; }
        [Serializable] private class GLightAnchors { public string attribute_origin; public GRoomAnchor[] rooms; }
        [Serializable] private class GExtras { public GLightAnchors light_anchors; }
        [Serializable] private class GNode { public string name; public int mesh = -1; public float[] matrix; public GExtras extras; }
        [Serializable] private class GAccessor { public int bufferView = -1; public int byteOffset; public int componentType; public int count; public string type; }
        [Serializable] private class GView { public int buffer; public int byteOffset; public int byteLength; }
        [Serializable] private class GTexInfo { public int index = -1; public int texCoord; }
        [Serializable] private class GPbr { public float[] baseColorFactor; public GTexInfo baseColorTexture; }
        [Serializable] private class GMaterial { public string name; public GPbr pbrMetallicRoughness; }
        [Serializable] private class GImage { public string mimeType; public int bufferView = -1; }
        [Serializable] private class Gltf { public GNode[] nodes; public GMesh[] meshes; public GAccessor[] accessors; public GView[] bufferViews; public GMaterial[] materials; public GImage[] images; }

        private sealed class Glb
        {
            public Gltf Doc;
            public byte[] Bin;
        }

        private static Glb ReadGlb(string path)
        {
            byte[] data = File.ReadAllBytes(path);
            if (data.Length < 28 || Encoding.ASCII.GetString(data, 0, 4) != "glTF" || BitConverter.ToUInt32(data, 4) != 2)
                throw new InvalidDataException($"{path}: not a glTF 2.0 binary");
            int jsonLength = (int)BitConverter.ToUInt32(data, 12);
            if (Encoding.ASCII.GetString(data, 16, 4) != "JSON")
                throw new InvalidDataException($"{path}: first chunk is not JSON");
            string json = Encoding.UTF8.GetString(data, 20, jsonLength);
            int binHeader = 20 + jsonLength;
            int binLength = (int)BitConverter.ToUInt32(data, binHeader);
            if (Encoding.ASCII.GetString(data, binHeader + 4, 4) != "BIN\0")
                throw new InvalidDataException($"{path}: second chunk is not BIN");
            var bin = new byte[binLength];
            Buffer.BlockCopy(data, binHeader + 8, bin, 0, binLength);
            return new Glb { Doc = JsonUtility.FromJson<Gltf>(json), Bin = bin };
        }

        private static Vector3[] ReadVec3(Glb glb, int accessorIndex)
        {
            GAccessor a = glb.Doc.accessors[accessorIndex];
            if (a.componentType != 5126 || a.type != "VEC3")
                throw new InvalidDataException($"accessor {accessorIndex}: expected float VEC3");
            GView v = glb.Doc.bufferViews[a.bufferView];
            int offset = v.byteOffset + a.byteOffset;
            var floats = new float[a.count * 3];
            Buffer.BlockCopy(glb.Bin, offset, floats, 0, floats.Length * 4);
            var result = new Vector3[a.count];
            for (int i = 0; i < a.count; i++)
            {
                // glTF (x, y, z) right-handed → Unity (x, y, -z) left-handed.
                result[i] = new Vector3(floats[3 * i], floats[3 * i + 1], -floats[3 * i + 2]);
            }

            return result;
        }

        private static Vector2[] ReadVec2(Glb glb, int accessorIndex)
        {
            GAccessor a = glb.Doc.accessors[accessorIndex];
            if (a.componentType != 5126 || a.type != "VEC2")
                throw new InvalidDataException($"accessor {accessorIndex}: expected float VEC2");
            GView v = glb.Doc.bufferViews[a.bufferView];
            int offset = v.byteOffset + a.byteOffset;
            var floats = new float[a.count * 2];
            Buffer.BlockCopy(glb.Bin, offset, floats, 0, floats.Length * 4);
            var result = new Vector2[a.count];
            for (int i = 0; i < a.count; i++) result[i] = new Vector2(floats[2 * i], floats[2 * i + 1]);
            return result;
        }

        private static Texture2D LoadPng(Glb glb, GImage img, string assetPath, bool repeat)
        {
            if (img == null || img.bufferView < 0 || img.mimeType != "image/png") return null;
            GView v = glb.Doc.bufferViews[img.bufferView];
            var png = new byte[v.byteLength];
            Buffer.BlockCopy(glb.Bin, v.byteOffset, png, 0, v.byteLength);
            var tex = new Texture2D(2, 2, TextureFormat.RGBA32, true);
            if (!tex.LoadImage(png)) return null;
            // One shared atlas must clamp so a tile does not sample its neighbour.
            // One image per material repeats at the UV scale (metres / tile_m).
            tex.wrapMode = repeat ? TextureWrapMode.Repeat : TextureWrapMode.Clamp;
            tex.name = Path.GetFileNameWithoutExtension(assetPath);
            AssetDatabase.CreateAsset(tex, assetPath);
            return tex;
        }

        private static int[] ReadIndices(Glb glb, int accessorIndex)
        {
            GAccessor a = glb.Doc.accessors[accessorIndex];
            GView v = glb.Doc.bufferViews[a.bufferView];
            int offset = v.byteOffset + a.byteOffset;
            var result = new int[a.count];
            if (a.componentType == 5125)
            {
                Buffer.BlockCopy(glb.Bin, offset, result, 0, a.count * 4);
            }
            else if (a.componentType == 5123)
            {
                for (int i = 0; i < a.count; i++) result[i] = BitConverter.ToUInt16(glb.Bin, offset + 2 * i);
            }
            else
            {
                throw new InvalidDataException($"accessor {accessorIndex}: unsupported index type {a.componentType}");
            }

            // Mirroring z flips handedness, so the winding is reversed to keep faces outward.
            for (int t = 0; t + 2 < result.Length; t += 3)
            {
                (result[t + 1], result[t + 2]) = (result[t + 2], result[t + 1]);
            }

            return result;
        }

        private static Mesh BuildMesh(Glb glb, GMesh gmesh, string name, out int[] materialIndices)
        {
            var positions = new List<Vector3>();
            var normals = new List<Vector3>();
            var uvs = new List<Vector2>();
            var submeshes = new List<int[]>();
            var mats = new List<int>();
            bool anyUv = false;
            foreach (GPrimitive prim in gmesh.primitives)
            {
                if (prim.mode != 4) throw new InvalidDataException($"{name}: only triangle primitives are supported");
                int baseVertex = positions.Count;
                Vector3[] primPositions = ReadVec3(glb, prim.attributes.POSITION);
                positions.AddRange(primPositions);
                if (prim.attributes.NORMAL >= 0) normals.AddRange(ReadVec3(glb, prim.attributes.NORMAL));
                if (prim.attributes.TEXCOORD_0 >= 0)
                {
                    uvs.AddRange(ReadVec2(glb, prim.attributes.TEXCOORD_0));
                    anyUv = true;
                }
                else
                {
                    for (int i = 0; i < primPositions.Length; i++) uvs.Add(Vector2.zero);
                }
                int[] idx = ReadIndices(glb, prim.indices);
                for (int i = 0; i < idx.Length; i++) idx[i] += baseVertex;
                submeshes.Add(idx);
                mats.Add(prim.material);
            }

            var mesh = new Mesh { name = name };
            mesh.indexFormat = positions.Count > 65535 ? IndexFormat.UInt32 : IndexFormat.UInt16;
            mesh.SetVertices(positions);
            if (normals.Count == positions.Count) mesh.SetNormals(normals);
            // A rim without coordinates used to drop the name UVs on the same mesh.
            if (anyUv && uvs.Count == positions.Count) mesh.SetUVs(0, uvs);
            mesh.subMeshCount = submeshes.Count;
            for (int s = 0; s < submeshes.Count; s++) mesh.SetTriangles(submeshes[s], s, false);
            mesh.RecalculateBounds();
            materialIndices = mats.ToArray();
            return mesh;
        }

        // PP_Sprite_Lit multiplies by vertex alpha. A missing channel is unsafe:
        // alpha 0 hides the pane from both sides. White keeps the texture alpha.
        private static void SetWhiteVertexColors(Mesh mesh)
        {
            var colors = new Color[mesh.vertexCount];
            for (int i = 0; i < colors.Length; i++) colors[i] = Color.white;
            mesh.SetColors(colors);
        }

        private static bool IsViewGlass(GMaterial material) =>
            material != null && material.name == ViewGlassMaterial;

        private static void ApplyMatrix(Transform t, float[] m)
        {
            if (m == null || m.Length != 16) return;
            var g = new Matrix4x4();
            for (int c = 0; c < 4; c++)
                for (int r = 0; r < 4; r++)
                    g[r, c] = m[c * 4 + r];
            float[] s = { 1f, 1f, -1f, 1f };
            var u = new Matrix4x4();
            for (int c = 0; c < 4; c++)
                for (int r = 0; r < 4; r++)
                    u[r, c] = g[r, c] * s[r] * s[c];
            Vector3 x = u.GetColumn(0), y = u.GetColumn(1), z = u.GetColumn(2);
            t.localPosition = u.GetColumn(3);
            t.localRotation = Quaternion.LookRotation(z.normalized, y.normalized);
            t.localScale = new Vector3(x.magnitude, y.magnitude, z.magnitude);
        }

        // One mesh per (ground cell, material); a node goes to the cell holding its bounds
        // centre on glTF (x, z), as onward_generator.perf.measure.combine_groups. Collision
        // is not touched. Measured on the Quest in phase 05 (ADR-005).
        private static int BuildCombinedVisual(Glb visual, Mesh[] meshes, int[][] meshMaterials, Material[] materials,
            Transform parent, string root, float cellM, StaticEditorFlags flags, Options opt)
        {
            var groups = new SortedDictionary<(int, int, int), List<CombineInstance>>();
            var probe = new GameObject("probe");
            try
            {
                foreach (GNode node in visual.Doc.nodes.Where(n => n.mesh >= 0))
                {
                    probe.transform.position = Vector3.zero;
                    probe.transform.rotation = Quaternion.identity;
                    probe.transform.localScale = Vector3.one;
                    ApplyMatrix(probe.transform, node.matrix);
                    Matrix4x4 m = probe.transform.localToWorldMatrix;
                    Mesh mesh = meshes[node.mesh];
                    Vector3 c = m.MultiplyPoint3x4(mesh.bounds.center);
                    int cx = Mathf.FloorToInt(c.x / cellM), cz = Mathf.FloorToInt(-c.z / cellM);
                    for (int s = 0; s < mesh.subMeshCount; s++)
                    {
                        var key = (cx, cz, meshMaterials[node.mesh][s]);
                        if (!groups.TryGetValue(key, out List<CombineInstance> list)) groups[key] = list = new List<CombineInstance>();
                        list.Add(new CombineInstance { mesh = mesh, subMeshIndex = s, transform = m });
                    }
                }
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(probe);
            }

            AssetDatabase.StartAssetEditing();
            var built = new List<(string, Mesh, int)>();
            try
            {
                foreach (KeyValuePair<(int, int, int), List<CombineInstance>> g in groups)
                {
                    (int cx, int cz, int mat) = g.Key;
                    string name = $"K{cx}_{cz}_m{mat}".Replace('-', 'n');
                    // CombineMeshes adds mesh.vertexCount for every instance. The submesh
                    // range is smaller, so using it leaves a 16-bit buffer that Unity then rejects.
                    long verts = g.Value.Sum(ci => (long)ci.mesh.vertexCount);
                    var combined = new Mesh { name = name, indexFormat = verts > 65535 ? IndexFormat.UInt32 : IndexFormat.UInt16 };
                    combined.CombineMeshes(g.Value.ToArray(), true, true, false);
                    combined.RecalculateBounds();
                    Unwrapping.GenerateSecondaryUVSet(combined);
                    if (mat >= 0 && mat < visual.Doc.materials.Length && IsViewGlass(visual.Doc.materials[mat]))
                        SetWhiteVertexColors(combined);
                    AssetDatabase.CreateAsset(combined, $"{root}/Meshes/{name}.asset");
                    built.Add((name, combined, mat));
                }
            }
            finally
            {
                AssetDatabase.StopAssetEditing();
            }

            var byScale = new SortedDictionary<float, int>();
            foreach ((string name, Mesh mesh, int mat) in built)
            {
                var go = new GameObject(name);
                go.transform.SetParent(parent, false);
                go.AddComponent<MeshFilter>().sharedMesh = mesh;
                var renderer = go.AddComponent<MeshRenderer>();
                renderer.sharedMaterial = materials[mat];
                renderer.shadowCastingMode = ShadowCastingMode.On;
                GameObjectUtility.SetStaticEditorFlags(go, LightmapFlags(flags, renderer.sharedMaterials));
                renderer.scaleInLightmap = ContributesGi(renderer.sharedMaterial) ? ScaleInLightmap(opt, visual.Doc.materials, mat) : 0f;
                byScale[renderer.scaleInLightmap] = byScale.TryGetValue(renderer.scaleInLightmap, out int seen) ? seen + 1 : 1;
            }

            Debug.Log($"{LogPrefix} Combined visual: {built.Count} meshes from {groups.Values.Sum(l => l.Count)} primitives, cell {cellM} m");
            Debug.Log($"{LogPrefix} scaleInLightmap (scale x renderers): "
                + string.Join(", ", byScale.Select(kv => string.Format(CultureInfo.InvariantCulture, "{0:0.###} x {1}", kv.Key, kv.Value))));
            return built.Count;
        }

        // ---- Entry points ------------------------------------------------------------------
        private static string SceneDir(Package pkg) => $"{WorkRoot}/{pkg.Manifest.package_id}";
        private static string ScenePath(Package pkg) => $"{SceneDir(pkg)}/{pkg.Manifest.package_id}.unity";

        [MenuItem("ONWARD Murcia Export/1. Build scene from export package")]
        public static void BuildScene() =>
            BuildSceneFromPaths(RequireArg("-exportPackage"), RequireArg("-exportReport"));

        /// <summary>Same as the batch <c>-executeMethod BuildScene</c> path, for the live Editor / MCP.</summary>
        public static void BuildSceneFromPaths(string packageDir, string reportOut)
        {
            RunGuarded(() =>
            {
                ApplyQuestGraphics();
                var clock = Stopwatch.StartNew();
                Package pkg = LoadPackage(packageDir);
                Options opt = pkg.Options;
                string root = SceneDir(pkg);
                if (AssetDatabase.IsValidFolder(root))
                    throw new InvalidOperationException($"{root} already exists in the Unity project; packages are built once (delete it explicitly to rebuild)");
                EnsureFolder(root + "/Meshes");
                EnsureFolder(root + "/Materials");
                string scenePath = ScenePath(pkg);

                Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                StaticEditorFlags flags = StaticEditorFlags.ContributeGI;
                if (opt.static_batching) flags |= StaticEditorFlags.BatchingStatic;
                if (opt.occlusion_culling) flags |= StaticEditorFlags.OccluderStatic | StaticEditorFlags.OccludeeStatic;

                var visualRoot = new GameObject("Visual");
                Glb visual = ReadGlb(pkg.Path("visual"));
                int imageCount = visual.Doc.images == null ? 0 : visual.Doc.images.Length;
                bool repeatTextures = imageCount > 1;
                var images = new Texture2D[imageCount];
                if (imageCount > 0) EnsureFolder(root + "/Textures");
                for (int i = 0; i < imageCount; i++)
                    images[i] = LoadPng(visual, visual.Doc.images[i], $"{root}/Textures/T{i:D2}.asset", repeatTextures);
                Material[] materials = visual.Doc.materials.Select(g =>
                {
                    int index = g.pbrMetallicRoughness != null && g.pbrMetallicRoughness.baseColorTexture != null
                        ? g.pbrMetallicRoughness.baseColorTexture.index : -1;
                    Texture2D tex = index >= 0 && index < images.Length ? images[index]
                        : (!repeatTextures && images.Length == 1 ? images[0] : null);
                    return GetOrCreateMaterial(g, root + "/Materials", tex);
                }).ToArray();
                var visualMeshes = new Mesh[visual.Doc.meshes.Length];
                var visualMaterials = new int[visual.Doc.meshes.Length][];
                AssetDatabase.StartAssetEditing();
                try
                {
                    for (int i = 0; i < visual.Doc.meshes.Length; i++)
                    {
                        Mesh mesh = BuildMesh(visual, visual.Doc.meshes[i], $"V{i:D4}", out visualMaterials[i]);
                        visualMeshes[i] = mesh;
                        if (opt.combine_cell_m > 0) continue;
                        Unwrapping.GenerateSecondaryUVSet(mesh);
                        if (visualMaterials[i].Any(k => k >= 0 && k < visual.Doc.materials.Length && IsViewGlass(visual.Doc.materials[k])))
                            SetWhiteVertexColors(mesh);
                        AssetDatabase.CreateAsset(mesh, $"{root}/Meshes/V{i:D4}.asset");
                    }
                }
                finally
                {
                    AssetDatabase.StopAssetEditing();
                }

                int visualObjects;
                if (opt.combine_cell_m > 0)
                {
                    visualObjects = BuildCombinedVisual(visual, visualMeshes, visualMaterials, materials, visualRoot.transform, root, opt.combine_cell_m, flags, opt);
                }
                else
                {
                    visualObjects = 0;
                    foreach (GNode node in visual.Doc.nodes.Where(n => n.mesh >= 0))
                    {
                        var go = new GameObject(node.name.Replace('/', '_'));
                        go.transform.SetParent(visualRoot.transform, false);
                        ApplyMatrix(go.transform, node.matrix);
                        go.AddComponent<MeshFilter>().sharedMesh = visualMeshes[node.mesh];
                        var renderer = go.AddComponent<MeshRenderer>();
                        renderer.sharedMaterials = visualMaterials[node.mesh].Select(k => materials[k]).ToArray();
                        renderer.shadowCastingMode = ShadowCastingMode.On;
                        GameObjectUtility.SetStaticEditorFlags(go, LightmapFlags(flags, renderer.sharedMaterials));
                        renderer.scaleInLightmap = renderer.sharedMaterials.Any(m => !ContributesGi(m)) ? 0f
                            : visualMaterials[node.mesh].Select(k => ScaleInLightmap(opt, visual.Doc.materials, k)).DefaultIfEmpty(1f).Max();
                        visualObjects++;
                    }
                }

                // Collision stays separate from the visual meshes: MeshCollider only, no renderer.
                var collisionRoot = new GameObject("Collision");
                Glb collision = ReadGlb(pkg.Path("collision"));
                var collisionMeshes = new Mesh[collision.Doc.meshes.Length];
                AssetDatabase.StartAssetEditing();
                try
                {
                    for (int i = 0; i < collision.Doc.meshes.Length; i++)
                    {
                        Mesh mesh = BuildMesh(collision, collision.Doc.meshes[i], $"C{i:D4}", out _);
                        AssetDatabase.CreateAsset(mesh, $"{root}/Meshes/C{i:D4}.asset");
                        collisionMeshes[i] = mesh;
                    }
                }
                finally
                {
                    AssetDatabase.StopAssetEditing();
                }

                int colliders = 0;
                var physicGroups = new Dictionary<SetPhysicMaterial.EPhysicMaterialType, Transform>();
                foreach (GNode node in collision.Doc.nodes.Where(n => n.mesh >= 0))
                {
                    SetPhysicMaterial.EPhysicMaterialType kind = PhysicKind(node.name);
                    if (!physicGroups.TryGetValue(kind, out Transform group))
                    {
                        var groupObject = new GameObject("Physic_" + kind);
                        groupObject.transform.SetParent(collisionRoot.transform, false);
                        var physic = groupObject.AddComponent<SetPhysicMaterial>();
                        physic.MaterialType = kind;
                        physic.IncludeChildren = true;
                        // Glass is the SDK penetrable type. AISeeThrough stays off every other collider:
                        // a concrete wall with that flag lets bots shoot through the whole facade.
                        physic.AISeeThrough = kind == SetPhysicMaterial.EPhysicMaterialType.Glass;
                        group = groupObject.transform;
                        physicGroups[kind] = group;
                    }
                    var go = new GameObject(node.name.Replace('/', '_'));
                    go.transform.SetParent(group, false);
                    ApplyMatrix(go.transform, node.matrix);
                    go.AddComponent<MeshCollider>().sharedMesh = collisionMeshes[node.mesh];
                    GameObjectUtility.SetStaticEditorFlags(go, StaticEditorFlags.NavigationStatic);
                    colliders++;
                }

                var sun = new GameObject("Directional Light");
                var light = sun.AddComponent<Light>();
                light.type = LightType.Directional;
                light.lightmapBakeType = LightmapBakeType.Baked;
                light.intensity = 1.2f;
                light.shadows = LightShadows.Soft;
                sun.transform.rotation = Quaternion.Euler(50f, -30f, 0f);
                ApplyFog(opt);

                float tallest = 0f;
                foreach (MeshCollider collider in collisionRoot.GetComponentsInChildren<MeshCollider>())
                    if (collider.bounds.max.y > tallest) tallest = collider.bounds.max.y;
                if (IsSchema(pkg.Layout.schema, "gameplay_layout"))
                    BakeNavigation(root, collisionRoot, pkg.Layout);
                AddGameplay(pkg.Layout, tallest);
                var probes = new List<Vector3>();
                AddInteriorLights(visual, opt, probes);
                AddLightProbes(probes, IsSchema(pkg.Layout.schema, "gameplay_layout"));
                EditorSceneManager.SaveScene(scene, scenePath);

                string sharedPath = $"{root}/SharedLevelSettings.asset";
                var shared = ScriptableObject.CreateInstance<SharedLevelSettings>();
                AssetDatabase.CreateAsset(shared, sharedPath);
                shared.Scene_PC = null;
                shared.Scene_Quest = AssetDatabase.LoadAssetAtPath<SceneAsset>(scenePath);
                shared.UseBuiltInSkybox = true;
                ApplyEnvironment(shared, pkg.Layout);
                EditorUtility.SetDirty(shared);

                GameObject descriptorObject = InstantiatePrefab("LevelDescriptor.prefab");
                var descriptor = descriptorObject.GetComponent<LevelDescriptor>();
                descriptor.SharedSettings = shared;
                descriptor.Title = pkg.Manifest.map.title;
                descriptor.Description = pkg.Manifest.map.description;
                Texture2D thumb = LoadListingThumbnail(pkg, root);
                if (thumb != null)
                {
                    shared.Thumbnail = thumb;
                    descriptor.Thumbnail = thumb;
                    EditorUtility.SetDirty(shared);
                }
                EditorUtility.SetDirty(descriptor);

                double buildSeconds = clock.Elapsed.TotalSeconds;
                var bakeClock = Stopwatch.StartNew();
                Bake(root, opt);
                double bakeSeconds = bakeClock.Elapsed.TotalSeconds;

                EditorSceneManager.MarkSceneDirty(scene);
                EditorSceneManager.SaveScene(scene, scenePath);
                AssetDatabase.SaveAssets();

                File.WriteAllText(reportOut, SceneReport(pkg, scenePath, visualObjects, colliders, buildSeconds, bakeSeconds));
                Debug.Log($"{LogPrefix} Scene saved: {scenePath}. Lightmaps: {LightmapSettings.lightmaps.Length}. Report: {reportOut}");
            });
        }

        [MenuItem("ONWARD Murcia Export/2. Build for Quest via Onward Launcher")]
        public static void BuildQuestViaLauncher() =>
            BuildLauncherFromPaths(RequireArg("-exportPackage"), RequireArg("-exportOut"));

        /// <summary>Same as the batch launcher step, for the live Editor / MCP.</summary>
        public static void BuildLauncherFromPaths(string packageDir, string outDir)
        {
            RunGuarded(() =>
            {
                Package pkg = LoadPackage(packageDir);
                if (Directory.Exists(outDir) && Directory.EnumerateFileSystemEntries(outDir).Any())
                    throw new InvalidOperationException($"{outDir} is not empty; launcher output is never overwritten");
                Directory.CreateDirectory(outDir);
                string scenePath = ScenePath(pkg);
                if (AssetDatabase.LoadAssetAtPath<SceneAsset>(scenePath) == null)
                    throw new FileNotFoundException($"Scene {scenePath} not found; run the scene step first.");

                ApplyQuestGraphics();
                EditorPrefs.SetString(LauncherBinPathPref, outDir);
                EditorPrefs.SetInt(LauncherPlatformPref, LauncherPlatformQuest);
                EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);
                RestoreFullHuntRoster(pkg.Layout);
                PrepareHuntPatrol();
                if (EditorSceneManager.GetActiveScene().isDirty)
                    EditorSceneManager.SaveOpenScenes();

                string title = pkg.Manifest.map.title;
                string description = pkg.Manifest.map.description;
                WorkshopItem workshopItem = new WorkshopItem();
                var clock = Stopwatch.StartNew();
                // The SDK method hardcodes MapName "Unity Export". Pass the package title into the same build call.
                bool built = ContentBuilder.BuildScene(
                    title,
                    description,
                    LevelDescriptor.EMapEnvironment.Desert,
                    LevelDescriptor.EMapSize.Medium,
                    LevelDescriptor.ETimeOfDay.Day,
                    "unity-export.local",
                    "0.1",
                    BuildTarget.Android,
                    ref workshopItem);
                if (!built)
                    throw new InvalidOperationException($"ContentBuilder.BuildScene failed for {pkg.Manifest.package_id}");

                string bundlePath = Path.Combine(Application.dataPath + "/../", ContentBuilder.FullAssetBundlePath);
                GM_Base[] allModes = UnityEngine.Object.FindObjectsOfType<GM_Base>();
                GameVariantTypes[] modes = new GameVariantTypes[allModes.Length];
                for (int i = 0; i < allModes.Length; ++i)
                    modes[i] = (GameVariantTypes)(int)allModes[i].GameModeType;
                workshopItem.ID = "unity-export.local";
                workshopItem.GameModes = modes;
                workshopItem.MapName = title;
                workshopItem.Description = description;
                string contentFile = Path.Combine(outDir, "unity-export.local." + CustomContentUtils.CONTENT_EXTENSION);
                File.Copy(bundlePath, contentFile, true);
                workshopItem.BundleHash = OnwardCustomContent.CustomMapUploader.GetMD5Hash(contentFile);
                File.WriteAllText(
                    Path.Combine(outDir, "unity-export.local." + CustomContentUtils.INFO_EXTENSION),
                    JsonUtility.ToJson(new CachedWorkshopItem { IsSanitized = false, WorkshopItem = workshopItem }));
                Debug.Log($"{LogPrefix} Launcher MapName={title}");

                string content = Path.Combine(outDir, $"unity-export.local.{CustomContentUtils.CONTENT_EXTENSION}");
                string info = Path.Combine(outDir, $"unity-export.local.{CustomContentUtils.INFO_EXTENSION}");
                if (!File.Exists(content) || !File.Exists(info))
                    throw new FileNotFoundException($"Launcher did not produce both files in {outDir}.");
                Debug.Log($"{LogPrefix} Launcher output OK in {clock.Elapsed.TotalSeconds:F1} s: {content} ({new FileInfo(content).Length} bytes)");
            });
        }

        // Routes stay in Loops. The prefab default is on. A return written
        // into the point list did not send the bot back on the Quest. A second
        // launcher also puts the baker back when the previous bundle save
        // removed it, so the navigation JSON is not wiped.
        private static void PrepareHuntPatrol()
        {
            WaypointSet[] routes = UnityEngine.Object.FindObjectsOfType<WaypointSet>();
            int changed = 0;
            for (int i = 0; i < routes.Length; i++)
            {
                if (routes[i].Loops) continue;
                routes[i].Loops = true;
                PrefabUtility.RecordPrefabInstancePropertyModifications(routes[i]);
                EditorUtility.SetDirty(routes[i]);
                changed++;
            }
            Debug.Log($"{LogPrefix} Hunt routes {routes.Length}: loops on, changed {changed}.");
            EnsureLightmapsAssigned();
            if (routes.Length > 0 && UnityEngine.Object.FindObjectOfType<CustomContentNavigationMapGenerator>() == null)
            {
                string scenePath = EditorSceneManager.GetActiveScene().path.Replace('\\', '/');
                string assetPath = Path.GetDirectoryName(scenePath)?.Replace('\\', '/') + "/HuntNavMap.asset";
                var map = AssetDatabase.LoadAssetAtPath<CustomContentNavigationMap>(assetPath);
                if (map == null || map.Nodes == null || map.Nodes.Count == 0)
                    throw new InvalidOperationException($"Hunt navigation asset has no nodes at {assetPath}.");
                var restored = new GameObject("NavigationMap");
                restored.AddComponent<CustomContentNavigationMapGenerator>().NavMap = map;
                Debug.Log($"{LogPrefix} Restored NavigationMap from {assetPath} ({map.Nodes.Count} nodes).");
            }
            int keepBots = StableHuntBots;
            int requestedBots;
            if (int.TryParse(OptionalArg("-huntBots"), out requestedBots) && requestedBots >= 1 && requestedBots <= 32)
                keepBots = requestedBots;
            KeepHuntBots(keepBots);
            SnapRoutePointsOntoNavMesh();
            RestoreReturnLinks();
            float hole = AnchorWaypointsOutsideNodeGrid();
            ValidateHuntNavigation(hole);
            var scene = EditorSceneManager.GetActiveScene();
            EditorSceneManager.MarkSceneDirty(scene);
            EditorSceneManager.SaveScene(scene);
        }

        // Keeps the first `keep` riflemen and routes. Loops stays on.
        private static void KeepHuntBots(int keep)
        {
            var hunt = UnityEngine.Object.FindObjectOfType<ObjectiveOpHunt>();
            if (hunt == null || hunt.AI_SpawnPoints == null || hunt.AI_SpawnPoints.Count < keep)
                throw new InvalidOperationException("Hunt objective needs more AI spawns than the probe keeps.");
            var keptSpawns = new List<AI_Spawnpoint>();
            int riflemen = 0;
            for (int i = 0; i < keep; i++)
            {
                AI_Spawnpoint point = hunt.AI_SpawnPoints[i];
                // The sniper probe wrote BotClass on the prefab instance. Setting
                // the field without recording left every bot except AI_0 as a sniper,
                // and those stood still on the Quest.
                point.BotClass = AI_Spawnpoint.BotClassType.Rifleman;
                if (PrefabUtility.IsPartOfPrefabInstance(point))
                    PrefabUtility.RecordPrefabInstancePropertyModifications(point);
                EditorUtility.SetDirty(point);
                if (point.BotClass == AI_Spawnpoint.BotClassType.Rifleman) riflemen++;
                keptSpawns.Add(point);
            }
            int removedBots = 0;
            for (int i = hunt.AI_SpawnPoints.Count - 1; i >= keep; i--)
            {
                AI_Spawnpoint extra = hunt.AI_SpawnPoints[i];
                if (extra == null) continue;
                UnityEngine.Object.DestroyImmediate(extra.gameObject);
                removedBots++;
            }
            int routePoints;
            int.TryParse(OptionalArg("-huntRoutePoints"), out routePoints);
            var keptRoutes = new List<WaypointSet>();
            int removedRoutes = 0;
            string keptPointCounts = "";
            if (hunt.AI_WaypointSet != null)
            {
                int routeKeep = Math.Min(keep, hunt.AI_WaypointSet.Count);
                int requestedRoutes;
                if (int.TryParse(OptionalArg("-huntRoutes"), out requestedRoutes) && requestedRoutes >= 0)
                    routeKeep = Math.Min(requestedRoutes, hunt.AI_WaypointSet.Count);
                for (int i = 0; i < routeKeep; i++)
                {
                    WaypointSet route = hunt.AI_WaypointSet[i];
                    if (route == null) continue;
                    route.Loops = true;
                    if (routePoints >= 2 && route.Points != null && route.Points.Count > routePoints)
                        route.Points = route.Points.GetRange(0, routePoints);
                    keptPointCounts += (keptPointCounts.Length == 0 ? "" : ",") + (route.Points == null ? 0 : route.Points.Count);
                    if (PrefabUtility.IsPartOfPrefabInstance(route))
                        PrefabUtility.RecordPrefabInstancePropertyModifications(route);
                    EditorUtility.SetDirty(route);
                    keptRoutes.Add(route);
                }
                for (int i = hunt.AI_WaypointSet.Count - 1; i >= routeKeep; i--)
                {
                    WaypointSet extra = hunt.AI_WaypointSet[i];
                    if (extra == null) continue;
                    UnityEngine.Object.DestroyImmediate(extra.gameObject);
                    removedRoutes++;
                }
            }
            hunt.AI_SpawnPoints = keptSpawns;
            hunt.AI_WaypointSet = keptRoutes;
            if (PrefabUtility.IsPartOfPrefabInstance(hunt))
                PrefabUtility.RecordPrefabInstancePropertyModifications(hunt);
            EditorUtility.SetDirty(hunt);
            Debug.Log($"{LogPrefix} Hunt diagnostic: {keptSpawns.Count} spawns kept, {riflemen} riflemen, removed {removedBots} spawns and {removedRoutes} routes, route points [{keptPointCounts}].");
        }

        private static void EnsureLightmapsAssigned()
        {
            if (LightmapSettings.lightmaps != null && LightmapSettings.lightmaps.Length > 0)
            {
                Debug.Log($"{LogPrefix} Lightmaps assigned: {LightmapSettings.lightmaps.Length}.");
                return;
            }
            string scenePath = EditorSceneManager.GetActiveScene().path.Replace('\\', '/');
            string dir = Path.GetDirectoryName(scenePath)?.Replace('\\', '/') + "/" + Path.GetFileNameWithoutExtension(scenePath);
            var maps = new List<LightmapData>();
            for (int i = 0; i < 8; i++)
            {
                var tex = AssetDatabase.LoadAssetAtPath<Texture2D>($"{dir}/Lightmap-{i}_comp_light.exr");
                if (tex == null) break;
                maps.Add(new LightmapData { lightmapColor = tex });
            }
            if (maps.Count == 0)
            {
                Debug.LogWarning($"{LogPrefix} No lightmaps assigned and no Lightmap files beside {scenePath}.");
                return;
            }
            LightmapSettings.lightmaps = maps.ToArray();
            EditorSceneManager.MarkSceneDirty(EditorSceneManager.GetActiveScene());
            Debug.Log($"{LogPrefix} Assigned {maps.Count} existing lightmaps.");
        }

        // ---- Scene pieces --------------------------------------------------------------------
        private static Vector3 Local(float x, float y, float z) => new Vector3(x, z, y);

        private static void AddGameplay(Layout layout, float tallestColliderM)
        {
            GameObject tent = InstantiatePrefab("Tent.prefab");
            tent.transform.position = Local(layout.attackers.centre_m[0], layout.attackers.centre_m[1], layout.tent_z_m);

            GameObject mapQuad = InstantiatePrefab("Map Quad.prefab");
            mapQuad.transform.position = Local(layout.map_quad_m[0], layout.map_quad_m[1], 0f);
            mapQuad.transform.rotation = Quaternion.identity;
            var quad = mapQuad.GetComponent<MapQuad>();
            quad.LevelSize = layout.level_size_m;
            // Flyover ceiling is LevelHeight/2 (SDK FlyoverSystem.GetToolCeiling).
            // Keep that plane 20 m above the tallest collider so a jet clears the tower.
            float ceiling = Mathf.Max(tallestColliderM + 20f, 40f);
            quad.LevelHeight = ceiling * 2f;

            GameObject damage = InstantiatePrefab("DamageVolume.prefab");
            Damage d = layout.damage_volume;
            damage.transform.position = Local(d.centre_m[0], d.centre_m[1], d.z_m);
            damage.transform.localScale = new Vector3(d.size_m[0], d.size_m[1], d.size_m[2]);

            SpawnSystem attackers = CreateSpawnSystem("SpawnsAttacking", layout.attackers);
            SpawnSystem defenders = CreateSpawnSystem("SpawnsDefending", layout.defenders);

            GameObject objective = InstantiatePrefab("Objectives/Obj_Uplink.prefab");
            objective.transform.position = Local(layout.uplink_m[0], layout.uplink_m[1], 0f);
            var uplink = objective.GetComponent<ObjectiveUplink>();
            uplink.AttackingTeamSpawns = new[] { attackers };
            uplink.DefendingTeamSpawns = new[] { defenders };

            GameObject freeRoam = InstantiatePrefab("Game Modes/GM_FreeRoam.prefab");
            var freeRoamMode = freeRoam.GetComponent<GM_FreeRoam>();
            freeRoamMode.AttackingTeamSpawns = new[] { attackers };
            freeRoamMode.DefendingTeamSpawns = new[] { defenders };

            GameObject uplinkMode = InstantiatePrefab("Game Modes/GM_Uplink.prefab");
            uplinkMode.GetComponent<GM_Uplink>().Objectives = new[] { uplink };

            if (IsSchema(layout.schema, "gameplay_layout"))
                AddPlayableLayer(layout, attackers, defenders);
        }

        private static SpawnSystem CreateSpawnSystem(string name, Block block)
        {
            GameObject go = InstantiatePrefab("SpawnSystem.prefab");
            go.name = name;
            go.transform.position = Local(block.centre_m[0], block.centre_m[1], SpawnHeight);
            var system = go.GetComponent<SpawnSystem>();
            Spawnpoint[] points = system.AllSpawns;
            if (points.Length < 5 || points.Length > block.points_m.Length)
                throw new InvalidOperationException($"{name}: prefab has {points.Length} spawn points; layout has {block.points_m.Length}.");
            for (int i = 0; i < points.Length; i++)
            {
                points[i].transform.position = Local(block.points_m[i].x, block.points_m[i].y, SpawnHeight);
                points[i].transform.rotation = Quaternion.Euler(0f, block.points_m[i].yaw, 0f);
            }

            return system;
        }

        private static SetPhysicMaterial.EPhysicMaterialType PhysicKind(string nodeName)
        {
            string name = (nodeName ?? "").ToLowerInvariant();
            if (name.Contains("water")) return SetPhysicMaterial.EPhysicMaterialType.Water;
            if (name.Contains("green") || name.Contains("grass")) return SetPhysicMaterial.EPhysicMaterialType.Grass;
            // The suffix is the collision node, not a prop whose id happens to contain "glass".
            if (name.Contains("#glass")) return SetPhysicMaterial.EPhysicMaterialType.Glass;
            return SetPhysicMaterial.EPhysicMaterialType.Concrete;
        }

        // ADR-014. Unity fog is measured from the camera. Whether the Onward runtime or its
        // post-processing profile overrides these RenderSettings is not known; checked on the headset.
        private static void ApplyFog(Options opt)
        {
            RenderSettings.fog = opt.fog_enabled;
            if (!opt.fog_enabled) return;
            if (!(opt.fog_start_m >= 0f && opt.fog_end_m > opt.fog_start_m))
                throw new InvalidDataException($"fog_end_m ({opt.fog_end_m}) must be greater than fog_start_m ({opt.fog_start_m}) >= 0");
            Color colour;
            if (!ColorUtility.TryParseHtmlString(opt.fog_color, out colour))
                throw new InvalidDataException($"fog_color {opt.fog_color} is not #RRGGBB");
            RenderSettings.fogMode = FogMode.Linear;
            RenderSettings.fogStartDistance = opt.fog_start_m;
            RenderSettings.fogEndDistance = opt.fog_end_m;
            RenderSettings.fogColor = colour;
            Debug.Log($"{LogPrefix} Fog: linear {opt.fog_start_m}-{opt.fog_end_m} m, {opt.fog_color}.");
        }

        private static void ApplyEnvironment(SharedLevelSettings shared, Layout layout)
        {
            if (string.IsNullOrEmpty(layout.environment)) return;
            shared.MapEnvType = (LevelDescriptor.EMapEnvironment)Enum.Parse(typeof(LevelDescriptor.EMapEnvironment), layout.environment);
            shared.TimeOfDay = (LevelDescriptor.ETimeOfDay)Enum.Parse(typeof(LevelDescriptor.ETimeOfDay), layout.time_of_day);
            shared.PPProfileId = (LevelDescriptor.EProfileId)Enum.Parse(typeof(LevelDescriptor.EProfileId), layout.profile);
            if (layout.far_clip_m > 0f) shared.FarClipPlane = layout.far_clip_m;
            shared.UseBuiltInSkybox = true;
            shared.DefaultReverbPreset = AreaReverbPreset.SmoothOutdoor;
            // Guide v15: AreaReverb does nothing until this box is checked.
            if (IsSchema(layout.schema, "gameplay_layout"))
                shared.EnableReverbInjection = true;
        }

        private static bool WalkableGround(string nodeName)
        {
            string name = (nodeName ?? "").ToLowerInvariant();
            if (name.Contains("carriageway") || name.Contains("building") || name.Contains("urban")
                || name.Contains("landmark") || name.Contains("curb") || name.Contains("water"))
                return false;
            // Phase 03 names the ground mesh collider/walkable. It is not called pedestrian.
            // collider/walkable_crossing, the strips across the carriageway, matches
            // here too: it joins the blocks on both kerbs into one island (ADR-015).
            return name.Contains("walkable") || name.Contains("pedestrian") || name.Contains("sidewalk")
                || name.Contains("ground") || name.Contains("green");
        }

        private static void BakeNavigation(string root, GameObject collisionRoot, Layout layout)
        {
            var sources = new List<NavMeshBuildSource>();
            NavMeshBuilder.CollectSources(collisionRoot.transform, ~0, NavMeshCollectGeometry.PhysicsColliders, 0, new List<NavMeshBuildMarkup>(), sources);
            sources.RemoveAll(source => !WalkableGround(source.component != null ? source.component.gameObject.name : ""));
            if (sources.Count == 0)
                throw new InvalidOperationException("NavMesh collect found no walkable ground colliders.");
            // The walkable mesh still covers building footprints. Boxes are
            // dropped only where a ray hits the building mesh, so a rotated
            // footprint does not erase the sidewalk inside its bounding box.
            int blocked = NavMesh.GetAreaFromName("Not Walkable");
            if (blocked < 0) blocked = 1;
            BlockBuildingFootprints(collisionRoot, sources, blocked);
            Renderer[] renderers = collisionRoot.GetComponentsInChildren<Renderer>();
            Bounds bounds = renderers.Length > 0 ? renderers[0].bounds : new Bounds(collisionRoot.transform.position, Vector3.one * 20f);
            foreach (Renderer renderer in renderers) bounds.Encapsulate(renderer.bounds);
            foreach (MeshCollider collider in collisionRoot.GetComponentsInChildren<MeshCollider>())
            {
                if (collider.sharedMesh == null) continue;
                bounds.Encapsulate(collider.bounds);
            }
            bounds.Expand(4f);
            NavMeshBuildSettings settings = NavMesh.GetSettingsByID(0);
            settings.agentRadius = 0.35f;
            settings.agentHeight = 1.8f;
            settings.agentSlope = 30f;
            settings.agentClimb = 0.3f;
            settings.minRegionArea = 0.5f;
            NavMeshData data = NavMeshBuilder.BuildNavMeshData(settings, sources, bounds, Vector3.zero, Quaternion.identity);
            if (data == null) throw new InvalidOperationException("NavMeshBuilder returned no data.");
            data.name = "HuntNavMesh";
            AssetDatabase.CreateAsset(data, $"{root}/HuntNavMesh.asset");

            // Awake calls AddNavMeshData as soon as the object is active. On a
            // new active object that runs before NavMeshData is assigned, the
            // call clears the mesh and Unity disables the component.
            var bakerObject = new GameObject("NavigationMeshBaker");
            bakerObject.SetActive(false);
            var baker = bakerObject.AddComponent<NavigationMeshBaker>();
            baker.CollectType = NavMeshCollectGeometry.PhysicsColliders;
            baker.NavMeshData = data;
            baker.Bounds = bounds;
            baker.enabled = true;
            bakerObject.SetActive(true);
            baker.SetNavMesh();

            var map = ScriptableObject.CreateInstance<CustomContentNavigationMap>();
            AssetDatabase.CreateAsset(map, $"{root}/HuntNavMap.asset");
            var generator = new GameObject("NavigationMap").AddComponent<CustomContentNavigationMapGenerator>();
            generator.NavMap = map;
            // Guide: origin outside, near the centre. ReachableTest drops every
            // node that cannot path here, so it has to sit on the baked mesh.
            if (layout.uplink_m == null || layout.uplink_m.Length < 2)
                throw new InvalidOperationException("gameplay layout has no uplink for the navigation origin.");
            Vector3 origin = Snap(layout.uplink_m[0], layout.uplink_m[1], "nav origin");
            generator.NavMeshOrigin = origin;
            generator.OutDoorPoints = new List<Vector3> { origin };
            LogNavIslands(origin);
            RequireNavigationObject(generator, map);
            BakeNodesToFit(generator, map);
            EditorUtility.SetDirty(map);
        }

        // Diagnostic only: how many separate pieces the baked mesh has, and how
        // much of its area is on the origin's piece. GenerateNodes keeps only the
        // nodes on that piece, so a Hunt route elsewhere has nothing to walk on.
        // Triangles that share a vertex position (to the centimetre) are one
        // island; the origin's island is the one whose triangle centre is nearest.
        private static void LogNavIslands(Vector3 origin)
        {
            NavMeshTriangulation tri = NavMesh.CalculateTriangulation();
            var parent = new int[tri.vertices.Length];
            for (int i = 0; i < parent.Length; i++) parent[i] = i;
            var welded = new Dictionary<Vector3Int, int>();
            for (int i = 0; i < tri.vertices.Length; i++)
            {
                Vector3 v = tri.vertices[i];
                var key = new Vector3Int(Mathf.RoundToInt(v.x * 100f), Mathf.RoundToInt(v.y * 100f), Mathf.RoundToInt(v.z * 100f));
                int first;
                if (welded.TryGetValue(key, out first)) JoinIslands(parent, i, first);
                else welded[key] = i;
            }
            for (int i = 0; i + 2 < tri.indices.Length; i += 3)
            {
                JoinIslands(parent, tri.indices[i], tri.indices[i + 1]);
                JoinIslands(parent, tri.indices[i], tri.indices[i + 2]);
            }
            var areas = new Dictionary<int, float>();
            float total = 0f;
            float nearest = float.MaxValue;
            int home = -1;
            for (int i = 0; i + 2 < tri.indices.Length; i += 3)
            {
                Vector3 a = tri.vertices[tri.indices[i]];
                Vector3 b = tri.vertices[tri.indices[i + 1]];
                Vector3 c = tri.vertices[tri.indices[i + 2]];
                float area = Vector3.Cross(b - a, c - a).magnitude * 0.5f;
                int root = IslandOf(parent, tri.indices[i]);
                float sum;
                areas.TryGetValue(root, out sum);
                areas[root] = sum + area;
                total += area;
                float distance = ((a + b + c) / 3f - origin).sqrMagnitude;
                if (distance < nearest)
                {
                    nearest = distance;
                    home = root;
                }
            }
            float homeArea = home >= 0 ? areas[home] : 0f;
            Debug.Log($"{LogPrefix} Navmesh islands: {areas.Count}. The origin island holds {homeArea:F0} of {total:F0} m² ({100f * homeArea / Mathf.Max(total, 1f):F0}%); GenerateNodes keeps only that island.");
        }

        private static int IslandOf(int[] parent, int i)
        {
            while (parent[i] != i)
            {
                parent[i] = parent[parent[i]];
                i = parent[i];
            }
            return i;
        }

        private static void JoinIslands(int[] parent, int a, int b)
        {
            int ra = IslandOf(parent, a);
            int rb = IslandOf(parent, b);
            if (ra != rb) parent[Math.Max(ra, rb)] = Math.Min(ra, rb);
        }

        // Node spacing follows the baked mesh, not a fixed city. A straight
        // crossing and the ground area each propose a step; the larger one is
        // the start. GenerateNodes is repeated only while a walk that does not
        // remember visited nodes is still deeper than the Quest stack. The
        // scene step throws if it never fits, or if a link still points back.
        private static void BakeNodesToFit(CustomContentNavigationMapGenerator generator, CustomContentNavigationMap map)
        {
            float area;
            float span;
            SampleNavMesh(out area, out span);
            float spacing = InitialNavSpacing(area, span);
            for (int attempt = 1; attempt <= NavSpacingAttempts; attempt++)
            {
                ApplyNavSpacing(generator, spacing);
                generator.GenerateNodes();
                int dropped = DropCoincidentLinks(map);
                int reversed = OrientLinksOneWay(map);
                NavWalk walk = MeasureOpenWalk(map);
                Debug.Log($"{LogPrefix} Navigation spacing {spacing:F1} m, attempt {attempt}: {walk.nodes} nodes, depth {walk.depth}, one-way {reversed}, dropped {dropped}.");
                if (walk.depth <= MaxNavWalkDepth)
                    return;
                if (attempt == NavSpacingAttempts)
                    throw new InvalidOperationException(
                        $"Navigation walk depth {walk.depth} exceeds {MaxNavWalkDepth} after {attempt} attempts at {spacing:F1} m. The scene step stops here.");
                float factor = walk.depth / (float)MaxNavWalkDepth;
                float next = spacing * Mathf.Max(1.25f, Mathf.Sqrt(factor));
                if (next <= spacing) next = spacing + 0.5f;
                if (span > spacing && next > span) next = span;
                if (Mathf.Approximately(next, spacing))
                    throw new InvalidOperationException(
                        $"Navigation walk depth {walk.depth} exceeds {MaxNavWalkDepth} and spacing {spacing:F1} m cannot grow. The scene step stops here.");
                spacing = next;
            }
        }

        private static float InitialNavSpacing(float area, float span)
        {
            float fromSpan = span / MaxNavWalkDepth;
            float fromArea = Mathf.Sqrt(Mathf.Max(area, 1f) / NavNodeBudget);
            return Mathf.Max(1f, Mathf.Max(fromSpan, fromArea));
        }

        private static void ApplyNavSpacing(CustomContentNavigationMapGenerator generator, float spacing)
        {
            generator.PathPointDensity = spacing;
            generator.CoverPointDensity = spacing;
            generator.PathPointNeighborDistance = spacing * 1.5f;
        }

        private static void SampleNavMesh(out float area, out float span)
        {
            NavMeshTriangulation tri = NavMesh.CalculateTriangulation();
            area = 0f;
            bool any = false;
            Vector3 min = Vector3.zero;
            Vector3 max = Vector3.zero;
            for (int i = 0; i + 2 < tri.indices.Length; i += 3)
            {
                Vector3 a = tri.vertices[tri.indices[i]];
                Vector3 b = tri.vertices[tri.indices[i + 1]];
                Vector3 c = tri.vertices[tri.indices[i + 2]];
                area += Vector3.Cross(b - a, c - a).magnitude * 0.5f;
                if (!any)
                {
                    min = a;
                    max = a;
                    any = true;
                }
                min = Vector3.Min(min, Vector3.Min(a, Vector3.Min(b, c)));
                max = Vector3.Max(max, Vector3.Max(a, Vector3.Max(b, c)));
            }
            if (!any || area < 1f)
                throw new InvalidOperationException("NavMesh has no walkable area.");
            span = Mathf.Max(max.x - min.x, max.z - min.z);
            if (span < 1f) span = 1f;
        }

        private struct NavWalk
        {
            public int nodes;
            public int largest;
            public int depth;
        }

        // The Editor has the SDK scripts, so a scene can bake and still be the
        // object the Quest rejects. Fail here, before the launcher, when the
        // baker, the asset or the neighbor graph cannot be used.
        private static void RequireNavigationObject(CustomContentNavigationMapGenerator generator, CustomContentNavigationMap map)
        {
            if (generator == null)
                throw new InvalidOperationException("NavigationMap is missing its generator script.");
            Component[] components = generator.GetComponents<Component>();
            for (int i = 0; i < components.Length; i++)
            {
                if (components[i] == null)
                    throw new InvalidOperationException("NavigationMap has a missing script; the Quest would drop the navigation map.");
            }
            if (generator.NavMap != map)
                throw new InvalidOperationException("NavigationMap generator does not reference the baked navigation asset.");
        }

        private static int DropCoincidentLinks(CustomContentNavigationMap map)
        {
            if (map == null || map.Nodes == null || map.Nodes.Count == 0)
                throw new InvalidOperationException("Operations Hunt navigation map has no nodes.");
            int count = map.Nodes.Count;
            // The SDK samples every triangle edge and does not merge the copy
            // that the neighbour triangle places on the same point. Drop that
            // link. A second rejection would stop every city bake.
            int dropped = 0;
            for (int i = 0; i < count; i++)
            {
                NavigationNode node = map.Nodes[i];
                if (node.Neighbors == null)
                    throw new InvalidOperationException($"Navigation node {i} has no neighbor list.");
                int kept = 0;
                var nextNeighbors = new int[node.Neighbors.Length];
                for (int n = 0; n < node.Neighbors.Length; n++)
                {
                    int next = node.Neighbors[n];
                    if (next == i)
                        throw new InvalidOperationException($"Navigation node {i} lists itself.");
                    if (next < 0 || next >= count)
                        throw new InvalidOperationException($"Navigation node {i} points at {next}, outside 0..{count - 1}.");
                    Vector3 step = map.Nodes[next].Position() - node.Position();
                    if (step.sqrMagnitude < 0.05f * 0.05f)
                    {
                        dropped++;
                        continue;
                    }
                    nextNeighbors[kept++] = next;
                }
                if (kept == node.Neighbors.Length) continue;
                var trimmed = new int[kept];
                Array.Copy(nextNeighbors, trimmed, kept);
                node.Neighbors = trimmed;
                map.Nodes[i] = node;
            }
            return dropped;
        }

        // The SDK's CollectNeighbors writes both directions. OrientLinksOneWay
        // drops the return half before a new bake is measured. A saved map
        // puts that half back so the JSON can walk each edge either way.
        // Waypoints and bot counts stay as they are.
        private static int RestoreReturnLinks()
        {
            CustomContentNavigationMapGenerator generator = UnityEngine.Object.FindObjectOfType<CustomContentNavigationMapGenerator>();
            CustomContentNavigationMap map = generator != null ? generator.NavMap : null;
            if (map == null || map.Nodes == null || map.Nodes.Count == 0)
                throw new InvalidOperationException("Hunt navigation map has no nodes to restore return links.");
            int added = 0;
            int count = map.Nodes.Count;
            for (int i = 0; i < count; i++)
            {
                NavigationNode node = map.Nodes[i];
                int[] forward = node.Neighbors;
                if (forward == null) continue;
                for (int n = 0; n < forward.Length; n++)
                {
                    int next = forward[n];
                    if (next < 0 || next >= count || next == i) continue;
                    NavigationNode neighbor = map.Nodes[next];
                    int[] back = neighbor.Neighbors ?? Array.Empty<int>();
                    bool present = false;
                    for (int k = 0; k < back.Length; k++)
                    {
                        if (back[k] != i) continue;
                        present = true;
                        break;
                    }
                    if (present) continue;
                    var grown = new int[back.Length + 1];
                    Array.Copy(back, grown, back.Length);
                    grown[back.Length] = i;
                    neighbor.Neighbors = grown;
                    map.Nodes[next] = neighbor;
                    added++;
                }
            }
            EditorUtility.SetDirty(map);
            Debug.Log($"{LogPrefix} Return links restored: {added}.");
            return added;
        }

        // The two routes that walk sit within 2.7 m of a node. Route_1's points
        // sit 8.5 m and 8.8 m out, past the widest gap in the baked grid, so
        // restoring a link between those distant nodes never reaches the waypoint.
        // A node is added on that point and tied in both directions. The waypoint
        // list and the spawns stay where they are.
        private static float AnchorWaypointsOutsideNodeGrid()
        {
            CustomContentNavigationMapGenerator generator = UnityEngine.Object.FindObjectOfType<CustomContentNavigationMapGenerator>();
            CustomContentNavigationMap map = generator != null ? generator.NavMap : null;
            if (map == null || map.Nodes == null || map.Nodes.Count < 2)
                throw new InvalidOperationException("Hunt navigation map has no grid to anchor waypoints.");
            float hole = WidestNodeGap(map);
            var hunt = UnityEngine.Object.FindObjectOfType<ObjectiveOpHunt>();
            if (hunt == null || hunt.AI_WaypointSet == null)
                throw new InvalidOperationException("Hunt objective has no waypoint sets to anchor.");
            int added = 0;
            for (int r = 0; r < hunt.AI_WaypointSet.Count; r++)
            {
                WaypointSet route = hunt.AI_WaypointSet[r];
                if (route == null || route.Points == null) continue;
                int previous = -1;
                bool previousIsNew = false;
                // A dropped point shifts the rest down; logs name the point by its index in the package.
                int removed = 0;
                for (int i = 0; i < route.Points.Count; i++)
                {
                    int wp = i + removed;
                    Vector3 point = route.Points[i];
                    int nearest = NearestLinkedNode(map, point);
                    float gap = Vector3.Distance(point, NodePosition(map, nearest));
                    int node = nearest;
                    bool created = gap > hole;
                    if (created)
                    {
                        node = AppendWaypointNode(map, point, nearest);
                        if (LinkAlongNavPath(map, node) < 0)
                            Debug.LogWarning($"{LogPrefix} {route.name} WP-{wp} stays off the city grid: no navmesh path.");
                        added++;
                        Debug.Log($"{LogPrefix} {route.name} WP-{wp} anchored, nearest linked node was {gap:F2} m away.");
                    }
                    if (previous >= 0 && (created || previousIsNew) && !LinkNodesAlongPath(map, previous, node))
                    {
                        Debug.LogWarning($"{LogPrefix} {route.name} WP-{wp} dropped: no navmesh path from the previous point.");
                        route.Points.RemoveAt(i);
                        if (PrefabUtility.IsPartOfPrefabInstance(route))
                            PrefabUtility.RecordPrefabInstancePropertyModifications(route);
                        EditorUtility.SetDirty(route);
                        removed++;
                        i--;
                        continue;
                    }
                    previous = node;
                    previousIsNew = created;
                }
                if (route.Points.Count < 2)
                {
                    Debug.LogWarning($"{LogPrefix} {route.name} dropped: fewer than 2 points on a walkable path.");
                    UnityEngine.Object.DestroyImmediate(route.gameObject);
                    hunt.AI_WaypointSet.RemoveAt(r);
                    if (r < hunt.AI_SpawnPoints.Count && hunt.AI_SpawnPoints[r] != null)
                        UnityEngine.Object.DestroyImmediate(hunt.AI_SpawnPoints[r].gameObject);
                    if (r < hunt.AI_SpawnPoints.Count)
                        hunt.AI_SpawnPoints.RemoveAt(r);
                    if (PrefabUtility.IsPartOfPrefabInstance(hunt))
                        PrefabUtility.RecordPrefabInstancePropertyModifications(hunt);
                    EditorUtility.SetDirty(hunt);
                    r--;
                }
            }
            // Later routes insert nodes. A waypoint can then sit closer to a
            // node that was never tied to its own route. Re-snap after every
            // insertion and drop a step that still cannot be walked back.
            PruneStepsWithoutReturnPath(map, hunt, hole);
            EditorUtility.SetDirty(map);
            Debug.Log($"{LogPrefix} Waypoint nodes anchored: {added}, grid hole {hole:F2} m, routes {hunt.AI_WaypointSet.Count}.");
            return hole;
        }

        // The node a waypoint keeps is the nearest one, inside the grid hole,
        // that can walk to the previous waypoint and back. A navmesh link that
        // only joins the old nearest pair is not enough: the check that follows
        // looks again, and a newer corner can be closer.
        private static void PruneStepsWithoutReturnPath(CustomContentNavigationMap map, ObjectiveOpHunt hunt, float hole)
        {
            for (int pass = 0; pass < 16; pass++)
            {
                bool dropped = false;
                for (int r = 0; r < hunt.AI_WaypointSet.Count; r++)
                {
                    WaypointSet route = hunt.AI_WaypointSet[r];
                    if (route == null || route.Points == null) continue;
                    int previous = -1;
                    int removed = 0;
                    for (int i = 0; i < route.Points.Count; i++)
                    {
                        int node = previous < 0
                            ? NearestLinkedNode(map, route.Points[i])
                            : ConnectedNode(map, route.Points[i], previous, hole);
                        if (node < 0 && previous >= 0)
                        {
                            int raw = NearestLinkedNode(map, route.Points[i]);
                            if (LinkNodesAlongPath(map, previous, raw))
                                node = ConnectedNode(map, route.Points[i], previous, hole);
                        }
                        if (node < 0)
                        {
                            Debug.LogWarning($"{LogPrefix} {route.name} WP-{i + removed} dropped: no return path from the previous point (pass {pass + 1}).");
                            route.Points.RemoveAt(i);
                            if (PrefabUtility.IsPartOfPrefabInstance(route))
                                PrefabUtility.RecordPrefabInstancePropertyModifications(route);
                            EditorUtility.SetDirty(route);
                            dropped = true;
                            removed++;
                            i--;
                            continue;
                        }
                        previous = node;
                    }
                    if (route.Points.Count >= 2) continue;
                    Debug.LogWarning($"{LogPrefix} {route.name} dropped: fewer than 2 points on a walkable path.");
                    UnityEngine.Object.DestroyImmediate(route.gameObject);
                    hunt.AI_WaypointSet.RemoveAt(r);
                    if (r < hunt.AI_SpawnPoints.Count && hunt.AI_SpawnPoints[r] != null)
                        UnityEngine.Object.DestroyImmediate(hunt.AI_SpawnPoints[r].gameObject);
                    if (r < hunt.AI_SpawnPoints.Count)
                        hunt.AI_SpawnPoints.RemoveAt(r);
                    if (PrefabUtility.IsPartOfPrefabInstance(hunt))
                        PrefabUtility.RecordPrefabInstancePropertyModifications(hunt);
                    EditorUtility.SetDirty(hunt);
                    dropped = true;
                    r--;
                }
                if (!dropped) return;
            }
        }

        private static int ConnectedNode(CustomContentNavigationMap map, Vector3 point, int previous, float maxGap)
        {
            int best = -1;
            float bestStep = float.MaxValue;
            for (int i = 0; i < map.Nodes.Count; i++)
            {
                if (!HasNeighbors(map, i)) continue;
                float step = Vector3.Distance(point, NodePosition(map, i));
                if (step > maxGap || step >= bestStep) continue;
                if (Hops(map, previous, i) < 0 || Hops(map, i, previous) < 0) continue;
                bestStep = step;
                best = i;
            }
            return best;
        }

        // The hole is the one measured before the new nodes. Prune already
        // removed a step that cannot be walked back. A kept point that is
        // still farther than the hole, or still has no return path, stops the build.
        private static void ValidateHuntNavigation(float hole)
        {
            CustomContentNavigationMapGenerator generator = UnityEngine.Object.FindObjectOfType<CustomContentNavigationMapGenerator>();
            CustomContentNavigationMap map = generator != null ? generator.NavMap : null;
            if (map == null || map.Nodes == null || map.Nodes.Count < 2)
                throw new InvalidOperationException("Hunt navigation map has no grid to validate.");
            int oneWay = CountOneWayLinks(map);
            if (oneWay > 0)
                throw new InvalidOperationException($"Hunt navigation has {oneWay} one-way links. The return half is missing.");
            var hunt = UnityEngine.Object.FindObjectOfType<ObjectiveOpHunt>();
            if (hunt == null || hunt.AI_WaypointSet == null || hunt.AI_WaypointSet.Count == 0)
                throw new InvalidOperationException("Hunt objective has no waypoint sets to validate.");
            for (int r = 0; r < hunt.AI_WaypointSet.Count; r++)
            {
                WaypointSet route = hunt.AI_WaypointSet[r];
                if (route == null || route.Points == null || route.Points.Count == 0)
                    throw new InvalidOperationException($"Hunt route {r} has no points.");
                int previous = -1;
                for (int i = 0; i < route.Points.Count; i++)
                {
                    int node = previous < 0
                        ? NearestLinkedNode(map, route.Points[i])
                        : ConnectedNode(map, route.Points[i], previous, hole);
                    if (node < 0)
                        throw new InvalidOperationException($"{route.name} has no return path between WP-{i - 1} and WP-{i}.");
                    float gap = Vector3.Distance(route.Points[i], NodePosition(map, node));
                    if (gap > hole)
                        throw new InvalidOperationException($"{route.name} WP-{i} is {gap:F2} m from the nearest linked node. The grid hole is {hole:F2} m.");
                    previous = node;
                }
            }
            Debug.Log($"{LogPrefix} Hunt navigation checked: {map.Nodes.Count} nodes, grid hole {hole:F2} m, routes {hunt.AI_WaypointSet.Count}.");
        }

        private static int CountOneWayLinks(CustomContentNavigationMap map)
        {
            int missing = 0;
            int count = map.Nodes.Count;
            for (int i = 0; i < count; i++)
            {
                int[] forward = map.Nodes[i].Neighbors;
                if (forward == null) continue;
                for (int n = 0; n < forward.Length; n++)
                {
                    int next = forward[n];
                    if (next < 0 || next >= count || next == i) continue;
                    int[] back = map.Nodes[next].Neighbors;
                    bool present = false;
                    if (back != null)
                    {
                        for (int k = 0; k < back.Length; k++)
                        {
                            if (back[k] != i) continue;
                            present = true;
                            break;
                        }
                    }
                    if (!present) missing++;
                }
            }
            return missing;
        }

        private static int Hops(CustomContentNavigationMap map, int from, int to)
        {
            if (from == to) return 0;
            int count = map.Nodes.Count;
            var seen = new bool[count];
            var depth = new int[count];
            var queue = new Queue<int>();
            seen[from] = true;
            queue.Enqueue(from);
            while (queue.Count > 0)
            {
                int node = queue.Dequeue();
                int[] next = map.Nodes[node].Neighbors;
                if (next == null) continue;
                for (int n = 0; n < next.Length; n++)
                {
                    int neighbor = next[n];
                    if (neighbor < 0 || neighbor >= count || seen[neighbor]) continue;
                    seen[neighbor] = true;
                    depth[neighbor] = depth[node] + 1;
                    if (neighbor == to) return depth[neighbor];
                    queue.Enqueue(neighbor);
                }
            }
            return -1;
        }

        private static float WidestNodeGap(CustomContentNavigationMap map)
        {
            float widest = 0f;
            int count = map.Nodes.Count;
            for (int i = 0; i < count; i++)
            {
                if (!HasNeighbors(map, i)) continue;
                float nearest = float.MaxValue;
                Vector3 here = NodePosition(map, i);
                for (int j = 0; j < count; j++)
                {
                    if (i == j || !HasNeighbors(map, j)) continue;
                    float step = Vector3.Distance(here, NodePosition(map, j));
                    if (step < nearest) nearest = step;
                }
                if (nearest < float.MaxValue && nearest > widest) widest = nearest;
            }
            if (widest <= 0f)
                throw new InvalidOperationException("Hunt navigation nodes occupy one point.");
            return widest;
        }

        private static bool HasNeighbors(CustomContentNavigationMap map, int index)
        {
            int[] list = map.Nodes[index].Neighbors;
            return list != null && list.Length > 0;
        }

        private static int NearestLinkedNode(CustomContentNavigationMap map, Vector3 point)
        {
            int nearest = -1;
            float best = float.MaxValue;
            for (int i = 0; i < map.Nodes.Count; i++)
            {
                if (!HasNeighbors(map, i)) continue;
                float step = Vector3.Distance(point, NodePosition(map, i));
                if (step >= best) continue;
                best = step;
                nearest = i;
            }
            if (nearest < 0)
                throw new InvalidOperationException("Hunt navigation map has no linked nodes.");
            return nearest;
        }

        private static Vector3 NodePosition(CustomContentNavigationMap map, int index)
        {
            NavigationNode node = map.Nodes[index];
            return new Vector3(node.X, node.Y, node.Z);
        }

        private static int AppendWaypointNode(CustomContentNavigationMap map, Vector3 point, int nearest)
        {
            NavigationNode source = map.Nodes[nearest];
            int index = map.Nodes.Count;
            map.Nodes.Add(new NavigationNode
            {
                Index = index,
                IsEdge = false,
                X = point.x,
                Y = point.y,
                Z = point.z,
                Neighbors = Array.Empty<int>(),
                CoverDistanceStanding = source.CoverDistanceStanding == null ? new byte[8] : (byte[])source.CoverDistanceStanding.Clone(),
                CoverDistanceCrouching = source.CoverDistanceCrouching == null ? new byte[8] : (byte[])source.CoverDistanceCrouching.Clone(),
                AreaIndex = source.AreaIndex,
                AreaTypes = source.AreaTypes,
            });
            return index;
        }

        // A straight ray misses a waypoint that sits around a corner. The path
        // corners are on the mesh, so each step can be a neighbor link.
        private static int LinkAlongNavPath(CustomContentNavigationMap map, int node)
        {
            int walkable = NavMesh.GetAreaFromName("Walkable");
            int mask = walkable >= 0 ? 1 << walkable : NavMesh.AllAreas;
            Vector3 origin = NodePosition(map, node);
            int count = map.Nodes.Count;
            int target = -1;
            float targetStep = float.MaxValue;
            var path = new NavMeshPath();
            for (int i = 0; i < count; i++)
            {
                if (i == node || !HasNeighbors(map, i)) continue;
                float step = Vector3.Distance(origin, NodePosition(map, i));
                if (step >= targetStep) continue;
                if (!NavMesh.CalculatePath(origin, NodePosition(map, i), mask, path) || path.status != NavMeshPathStatus.PathComplete)
                    continue;
                targetStep = step;
                target = i;
            }
            if (target < 0) return -1;
            NavMesh.CalculatePath(origin, NodePosition(map, target), mask, path);
            int previous = node;
            Vector3[] corners = path.corners;
            for (int c = 1; c < corners.Length - 1; c++)
            {
                int mid = AppendWaypointNode(map, corners[c], target);
                LinkBoth(map, previous, mid);
                previous = mid;
            }
            LinkBoth(map, previous, target);
            return target;
        }

        private static bool LinkNodesAlongPath(CustomContentNavigationMap map, int from, int to)
        {
            if (from == to) return true;
            int walkable = NavMesh.GetAreaFromName("Walkable");
            int mask = walkable >= 0 ? 1 << walkable : NavMesh.AllAreas;
            var path = new NavMeshPath();
            if (!NavMesh.CalculatePath(NodePosition(map, from), NodePosition(map, to), mask, path) || path.status != NavMeshPathStatus.PathComplete)
                return false;
            int previous = from;
            Vector3[] corners = path.corners;
            for (int c = 1; c < corners.Length - 1; c++)
            {
                int mid = AppendWaypointNode(map, corners[c], to);
                LinkBoth(map, previous, mid);
                previous = mid;
            }
            LinkBoth(map, previous, to);
            return true;
        }

        private static bool LinkWouldBeClear(CustomContentNavigationMap map, int a, int b)
        {
            int walkable = NavMesh.GetAreaFromName("Walkable");
            int mask = walkable >= 0 ? 1 << walkable : NavMesh.AllAreas;
            NavMeshHit hit;
            return !NavMesh.Raycast(NodePosition(map, a), NodePosition(map, b), out hit, mask);
        }

        private static void LinkBoth(CustomContentNavigationMap map, int a, int b)
        {
            AddNeighbor(map, a, b);
            AddNeighbor(map, b, a);
        }

        private static bool LinkBothIfClear(CustomContentNavigationMap map, int a, int b)
        {
            if (a == b || a < 0 || b < 0) return false;
            if (!LinkWouldBeClear(map, a, b)) return false;
            LinkBoth(map, a, b);
            return true;
        }

        private static void AddNeighbor(CustomContentNavigationMap map, int from, int to)
        {
            NavigationNode node = map.Nodes[from];
            int[] list = node.Neighbors ?? Array.Empty<int>();
            for (int i = 0; i < list.Length; i++)
            {
                if (list[i] == to) return;
            }
            var grown = new int[list.Length + 1];
            Array.Copy(list, grown, list.Length);
            grown[list.Length] = to;
            node.Neighbors = grown;
            map.Nodes[from] = node;
        }

        // The SDK stores every street in both directions. The Quest walk does
        // not remember a node it already entered, so that pair never returns.
        // Keep the link only toward the higher index. Every step moves forward.
        private static int OrientLinksOneWay(CustomContentNavigationMap map)
        {
            int count = map.Nodes.Count;
            int reversed = 0;
            for (int i = 0; i < count; i++)
            {
                NavigationNode node = map.Nodes[i];
                int[] neighbors = node.Neighbors;
                int kept = 0;
                var nextNeighbors = new int[neighbors.Length];
                for (int n = 0; n < neighbors.Length; n++)
                {
                    int next = neighbors[n];
                    if (next <= i)
                    {
                        reversed++;
                        continue;
                    }
                    nextNeighbors[kept++] = next;
                }
                if (kept == neighbors.Length) continue;
                var trimmed = new int[kept];
                Array.Copy(nextNeighbors, trimmed, kept);
                node.Neighbors = trimmed;
                map.Nodes[i] = node;
            }
            return reversed;
        }

        // Longest chain a neighbor walk can hold when it does not remember
        // nodes it already entered. A link that points back does not end, so
        // the scene stops instead of shipping it.
        private static NavWalk MeasureOpenWalk(CustomContentNavigationMap map)
        {
            int count = map.Nodes.Count;
            var depthFrom = new int[count];
            int maxDepth = 0;
            for (int i = count - 1; i >= 0; i--)
            {
                int best = 1;
                int[] neighbors = map.Nodes[i].Neighbors;
                for (int n = 0; n < neighbors.Length; n++)
                {
                    int next = neighbors[n];
                    if (next <= i || next >= count)
                        throw new InvalidOperationException(
                            $"Navigation walk does not end: node {i} points at {next}. The scene step stops here.");
                    int via = depthFrom[next] + 1;
                    if (via > best) best = via;
                    if (via > count)
                        throw new InvalidOperationException("Navigation graph walk exceeded the node count.");
                }
                depthFrom[i] = best;
                if (best > maxDepth) maxDepth = best;
            }
            return new NavWalk { nodes = count, largest = maxDepth, depth = maxDepth };
        }

        private static void BlockBuildingFootprints(GameObject collisionRoot, List<NavMeshBuildSource> sources, int blocked)
        {
            const float step = 2f;
            foreach (MeshCollider collider in collisionRoot.GetComponentsInChildren<MeshCollider>())
            {
                string name = collider.gameObject.name.ToLowerInvariant();
                if (collider.sharedMesh == null || !(name.Contains("building") || name.Contains("landmark"))) continue;
                Bounds box = collider.bounds;
                float originY = box.max.y + 2f;
                float reach = box.size.y + 4f;
                for (float x = box.min.x; x <= box.max.x; x += step)
                {
                    for (float z = box.min.z; z <= box.max.z; z += step)
                    {
                        if (!collider.Raycast(new Ray(new Vector3(x, originY, z), Vector3.down), out _, reach)) continue;
                        sources.Add(new NavMeshBuildSource
                        {
                            shape = NavMeshBuildSourceShape.ModifierBox,
                            transform = Matrix4x4.TRS(new Vector3(x, 1f, z), Quaternion.identity, Vector3.one),
                            size = new Vector3(step + 0.6f, 4f, step + 0.6f),
                            area = blocked,
                        });
                    }
                }
            }
        }

        private static Vector3 Snap(float x, float y, string label)
        {
            Vector3 snapped;
            if (!TrySnap(x, y, out snapped))
                throw new InvalidOperationException($"{label} at ({x:F1}, {y:F1}) is not within 10 m of walkable navmesh.");
            return snapped;
        }

        private static bool TrySnap(float x, float y, out Vector3 snapped)
        {
            int walkable = NavMesh.GetAreaFromName("Walkable");
            int mask = walkable >= 0 ? 1 << walkable : NavMesh.AllAreas;
            NavMeshHit hit;
            if (!NavMesh.SamplePosition(Local(x, y, 0.3f), out hit, 10f, mask))
            {
                snapped = Vector3.zero;
                return false;
            }
            snapped = hit.position;
            return true;
        }

        // The SDK validator samples every waypoint except the last. A point that
        // only passes a 10 m search is still written onto the Walkable surface,
        // including that last index. The spawn of the same list entry stands on
        // the snapped start.
        private static void SnapRoutePointsOntoNavMesh()
        {
            NavigationMeshBaker baker = UnityEngine.Object.FindObjectOfType<NavigationMeshBaker>();
            if (baker == null || baker.NavMeshData == null)
                throw new InvalidOperationException("Hunt waypoints need a baked navigation mesh.");
            baker.SetNavMesh();
            int walkable = NavMesh.GetAreaFromName("Walkable");
            int mask = walkable >= 0 ? 1 << walkable : NavMesh.AllAreas;
            var hunt = UnityEngine.Object.FindObjectOfType<ObjectiveOpHunt>();
            List<WaypointSet> routes = hunt != null && hunt.AI_WaypointSet != null && hunt.AI_WaypointSet.Count > 0
                ? hunt.AI_WaypointSet
                : new List<WaypointSet>(UnityEngine.Object.FindObjectsOfType<WaypointSet>());
            int checkedPoints = 0;
            int moved = 0;
            float farthest = 0f;
            for (int r = 0; r < routes.Count; r++)
            {
                WaypointSet route = routes[r];
                if (route == null || route.Points == null || route.Points.Count < 2)
                    throw new InvalidOperationException($"Waypoint set {r} needs at least 2 points on the navmesh.");
                for (int i = 0; i < route.Points.Count; i++)
                {
                    Vector3 point = route.Points[i];
                    NavMeshHit hit;
                    if (!NavMesh.SamplePosition(point, out hit, 10f, mask))
                        throw new InvalidOperationException($"{route.name} WP-{i} is not within 10 m of walkable navmesh.");
                    checkedPoints++;
                    float shift = Vector3.Distance(point, hit.position);
                    if (shift > farthest) farthest = shift;
                    if (shift > 0.02f)
                    {
                        route.Points[i] = hit.position;
                        moved++;
                        Debug.Log($"{LogPrefix} {route.name} WP-{i} moved {shift:F2} m onto walkable navmesh.");
                    }
                    if (i > 0 && (route.Points[i] - route.Points[i - 1]).sqrMagnitude < MinLookDirectionM * MinLookDirectionM)
                        throw new InvalidOperationException($"{route.name} WP-{i} landed on WP-{i - 1}.");
                }
                if (PrefabUtility.IsPartOfPrefabInstance(route))
                    PrefabUtility.RecordPrefabInstancePropertyModifications(route);
                EditorUtility.SetDirty(route);
                if (hunt != null && hunt.AI_SpawnPoints != null && r < hunt.AI_SpawnPoints.Count && hunt.AI_SpawnPoints[r] != null)
                {
                    AI_Spawnpoint spawn = hunt.AI_SpawnPoints[r];
                    Vector3 start = route.Points[0];
                    if (Vector3.Distance(spawn.transform.position, start) > 0.02f)
                    {
                        spawn.transform.position = start;
                        if (PrefabUtility.IsPartOfPrefabInstance(spawn))
                            PrefabUtility.RecordPrefabInstancePropertyModifications(spawn);
                        EditorUtility.SetDirty(spawn);
                    }
                }
            }
            Debug.Log($"{LogPrefix} Hunt waypoints: {checkedPoints} points on walkable navmesh, moved {moved}, farthest {farthest:F2} m.");
        }

        // The saved scene dropped every agent past the third. The layout still
        // has the full roster, so the Quest build puts those riflemen back
        // before the navigation check.
        private static void RestoreFullHuntRoster(Layout layout)
        {
            if (layout == null || !IsSchema(layout.schema, "gameplay_layout")) return;
            var hunt = UnityEngine.Object.FindObjectOfType<ObjectiveOpHunt>();
            if (hunt == null)
                throw new InvalidOperationException("Hunt objective is missing; the scene step has to run first.");
            if (hunt.AI_SpawnPoints != null)
            {
                for (int i = 0; i < hunt.AI_SpawnPoints.Count; i++)
                {
                    if (hunt.AI_SpawnPoints[i] != null)
                        UnityEngine.Object.DestroyImmediate(hunt.AI_SpawnPoints[i].gameObject);
                }
            }
            if (hunt.AI_WaypointSet != null)
            {
                for (int i = 0; i < hunt.AI_WaypointSet.Count; i++)
                {
                    if (hunt.AI_WaypointSet[i] != null)
                        UnityEngine.Object.DestroyImmediate(hunt.AI_WaypointSet[i].gameObject);
                }
            }
            hunt.AI_SpawnPoints = new List<AI_Spawnpoint>();
            hunt.AI_WaypointSet = new List<WaypointSet>();
            List<WaypointSet> routes = BuildSnappedRoutes(layout);
            var spawns = new List<AI_Spawnpoint>();
            for (int i = 0; i < routes.Count; i++)
            {
                GameObject go = InstantiatePrefab("AI/AI_Spawnpoint.prefab");
                go.name = "AI_" + i;
                go.transform.position = routes[i].Points[0];
                float yaw = i < layout.ai_m.Length ? layout.ai_m[i].yaw : 0f;
                go.transform.rotation = Quaternion.Euler(0f, yaw, 0f);
                var point = go.GetComponent<AI_Spawnpoint>();
                point.BotClass = AI_Spawnpoint.BotClassType.Rifleman;
                spawns.Add(point);
            }
            hunt.AI_SpawnPoints = spawns;
            hunt.AI_WaypointSet = routes;
            if (PrefabUtility.IsPartOfPrefabInstance(hunt))
                PrefabUtility.RecordPrefabInstancePropertyModifications(hunt);
            EditorUtility.SetDirty(hunt);
            Debug.Log($"{LogPrefix} Hunt roster restored: {spawns.Count} riflemen, {routes.Count} routes.");
        }

        private static List<WaypointSet> BuildSnappedRoutes(Layout layout)
        {
            if (layout.ai_m == null || layout.ai_m.Length < 32)
                throw new InvalidOperationException("gameplay layout needs 32 AI points.");
            if (layout.routes == null || layout.routes.Length < 32)
                throw new InvalidOperationException("gameplay layout needs 32 Hunt routes, one per spawn.");
            NavigationMeshBaker baker = UnityEngine.Object.FindObjectOfType<NavigationMeshBaker>();
            if (baker == null || baker.NavMeshData == null)
                throw new InvalidOperationException("Hunt routes need a baked navigation mesh.");
            baker.SetNavMesh();
            var routes = new List<WaypointSet>();
            for (int r = 0; r < layout.routes.Length; r++)
            {
                XY[] pts = layout.routes[r].points;
                if (pts == null || pts.Length < 2) continue;
                GameObject go = InstantiatePrefab("AI/AI_WaypointSet.prefab");
                go.name = "Route_" + r;
                var set = go.GetComponent<WaypointSet>();
                set.Loops = true;
                set.Points = new List<Vector3>();
                for (int i = 0; i < pts.Length; i++)
                {
                    Vector3 snapped;
                    if (!TrySnap(pts[i].x, pts[i].y, out snapped)) continue;
                    if (set.Points.Count > 0 && (snapped - set.Points[set.Points.Count - 1]).sqrMagnitude < MinLookDirectionM * MinLookDirectionM)
                        continue;
                    set.Points.Add(snapped);
                }
                if (set.Points.Count >= 2
                    && (set.Points[0] - set.Points[set.Points.Count - 1]).sqrMagnitude < MinLookDirectionM * MinLookDirectionM)
                    set.Points.RemoveAt(set.Points.Count - 1);
                if (set.Points.Count < 2)
                {
                    UnityEngine.Object.DestroyImmediate(go);
                    continue;
                }
                routes.Add(set);
            }
            SnapRoutePointsOntoNavMesh();
            if (routes.Count < 32)
                throw new InvalidOperationException($"Hunt needs 32 routes on the navmesh; {routes.Count} kept two snapped points at least {MinLookDirectionM:F2} m apart.");
            Debug.Log($"{LogPrefix} Hunt routes {routes.Count}: loops on.");
            return routes;
        }

        private static void AddPlayableLayer(Layout layout, SpawnSystem attackers, SpawnSystem defenders)
        {
            if (layout.ai_m == null || layout.ai_m.Length < 32)
                throw new InvalidOperationException("gameplay layout needs 32 AI points.");
            if (layout.routes == null || layout.routes.Length < 32)
                throw new InvalidOperationException("gameplay layout needs 32 Hunt routes, one per spawn.");

            // Routes first. Each spawn is then the snapped first waypoint, so the
            // 5 m patrol link survives the navmesh snap and two bots cannot share it.
            List<WaypointSet> routes = BuildSnappedRoutes(layout);
            var spawns = new List<AI_Spawnpoint>();
            // Guide v15: only Rifleman spawns in game. Sniper and Automatic Rifleman are reserved.
            for (int i = 0; i < routes.Count; i++)
            {
                GameObject go = InstantiatePrefab("AI/AI_Spawnpoint.prefab");
                go.name = "AI_" + i;
                go.transform.position = routes[i].Points[0];
                float yaw = i < layout.ai_m.Length ? layout.ai_m[i].yaw : 0f;
                go.transform.rotation = Quaternion.Euler(0f, yaw, 0f);
                var point = go.GetComponent<AI_Spawnpoint>();
                point.BotClass = AI_Spawnpoint.BotClassType.Rifleman;
                spawns.Add(point);
            }

            if (layout.ammo_m == null || layout.ammo_m.Length < 4)
                throw new InvalidOperationException("gameplay layout needs 4 ammo boxes.");
            for (int i = 0; i < layout.ammo_m.Length; i++)
            {
                GameObject box = InstantiatePrefab("AmmoBox.prefab");
                box.name = "Ammo_" + i;
                box.transform.position = Snap(layout.ammo_m[i].x, layout.ammo_m[i].y, "ammo " + i);
            }

            GameObject huntObjective = InstantiatePrefab("Objectives/Obj_Op_Hunt.prefab");
            var hunt = huntObjective.GetComponent<ObjectiveOpHunt>();
            hunt.AttackingTeamSpawns = new[] { attackers };
            hunt.DefendingTeamSpawns = new SpawnSystem[0];
            hunt.AI_SpawnPoints = spawns;
            hunt.AI_WaypointSet = routes;
            KeepHuntBots(StableHuntBots);
            GameObject huntMode = InstantiatePrefab("Game Modes/GM_Op_Hunt.prefab");
            huntMode.GetComponent<GM_Operations_Hunt>().Objectives = new[] { hunt };
            // Onward 2.0.4's solo menu listed Hunt only when the scene contained
            // Free Roam, Hunt and Uplink. Assault, Escort, Evac and Retrieval stay
            // in the layout JSON; putting their prefabs in the scene left Free Roam
            // alone even after the .info3 card was shortened.

            if (!string.IsNullOrEmpty(layout.audio_global))
            {
                GameObject global = InstantiatePrefab("OnwardAudioSource.prefab");
                global.name = "Ambience";
                var source = global.GetComponent<OnwardAudioSource>();
                source.AudioSourceType = OnwardAudioType.GlobalAmbience;
                source.AudioSourceName = layout.audio_global;
            }
            PlaceLocalAudio("Birds", "Birds Park", layout.audio_birds_m);
            PlaceLocalAudio("TowerWind", "Wind Rooftop", layout.audio_wind_m);
            if (layout.audio_wind_m != null && layout.audio_wind_m.Length >= 2)
            {
                GameObject reverb = InstantiatePrefab("AreaReverb.prefab");
                reverb.name = "CathedralReverb";
                reverb.GetComponent<AreaReverb>().AreaPreset = AreaReverbPreset.ReflectiveIndoorLarge;
                reverb.transform.position = Local(layout.audio_wind_m[0], layout.audio_wind_m[1], 6f);
                reverb.transform.localScale = new Vector3(36f, 24f, 36f);
            }
            if (layout.flyover != 0)
            {
                GameObject flyover = InstantiatePrefab("FlyoverSystem.prefab");
                var system = flyover.GetComponent<FlyoverSystem>();
                system.EnabledInRegularMode = true;
                system.JetWeight = 1;
                system.HeliWeight = 1;
                system.BoatWeight = 0;
                system.JetBiomeMaterial = FlyoverBiomeMaterial.Urban;
                system.HeliBiomeMaterial = FlyoverBiomeMaterial.Urban;
                system.PhaseChance = 1f;
                MapQuad quad = UnityEngine.Object.FindObjectOfType<MapQuad>();
                if (quad != null) system.FindMapQuad(quad);
            }
        }

        // SDK v1.15 validators, not the Quest menu. Free Roam is already in AddGameplay.
        // Uplink is there too. This adds Assault (two spawn systems and one boundary
        // per team), Escort (two objective transforms), Evac (32 AI, defenders only,
        // one landing point) and Retrieval (the Hunt objective, that landing point,
        // hard-drive positions and a navmesh origin on the baked mesh).
        private static void AddMatchModes(
            Layout layout, SpawnSystem attackers, SpawnSystem defenders, List<AI_Spawnpoint> spawns, ObjectiveOpHunt hunt)
        {
            if (layout.schema_version != "0.3.0") return;
            if (layout.attackers_b == null || layout.defenders_b == null)
                throw new InvalidOperationException("gameplay layout 0.3.0 needs a second spawn block for each team.");
            SpawnSystem attackersB = CreateSpawnSystem("SpawnsAttackingB", layout.attackers_b);
            SpawnSystem defendersB = CreateSpawnSystem("SpawnsDefendingB", layout.defenders_b);

            GameObject assaultGo = InstantiatePrefab("Objectives/Obj_Assault.prefab");
            assaultGo.transform.position = Local(layout.uplink_m[0], layout.uplink_m[1], 0f);
            var assault = assaultGo.GetComponent<ObjectiveAssault>();
            assault.AttackingTeamSpawns = new[] { attackers, attackersB };
            assault.DefendingTeamSpawns = new[] { defenders, defendersB };
            var attackBoundary = InstantiatePrefab("AssaultMapBoundary.prefab").GetComponent<AssaultMapBoundary>();
            attackBoundary.gameObject.name = "AssaultBoundaryAttack";
            attackBoundary.transform.position = Local(layout.attackers.centre_m[0], layout.attackers.centre_m[1], 1f);
            var defendBoundary = InstantiatePrefab("AssaultMapBoundary.prefab").GetComponent<AssaultMapBoundary>();
            defendBoundary.gameObject.name = "AssaultBoundaryDefend";
            defendBoundary.transform.position = Local(layout.defenders.centre_m[0], layout.defenders.centre_m[1], 1f);
            assault.AttackingTeamBoundaries = new[] { attackBoundary };
            assault.DefendingTeamBoundaries = new[] { defendBoundary };
            InstantiatePrefab("Game Modes/GM_Assault.prefab").GetComponent<GM_Assault>().Objectives = new[] { assault };

            if (layout.escort_a_m == null || layout.escort_a_m.Length < 2 || layout.escort_b_m == null || layout.escort_b_m.Length < 2)
                throw new InvalidOperationException("gameplay layout 0.3.0 needs two Escort objective positions.");
            GameObject escortGo = InstantiatePrefab("Objectives/Obj_Escort.prefab");
            escortGo.transform.position = Local(layout.uplink_m[0], layout.uplink_m[1], 0f);
            var escort = escortGo.GetComponent<ObjectiveEscort>();
            escort.AttackingTeamSpawns = new[] { attackers };
            escort.DefendingTeamSpawns = new[] { defenders };
            escort.ObjectiveSpawnPosA = ModeMarker(escortGo.transform, "EscortA", layout.escort_a_m);
            escort.ObjectiveSpawnPosB = ModeMarker(escortGo.transform, "EscortB", layout.escort_b_m);
            InstantiatePrefab("Game Modes/GM_Escort.prefab").GetComponent<GM_Escort>().Objectives = new[] { escort };

            if (layout.evac_m == null || layout.evac_m.Length < 2)
                throw new InvalidOperationException("gameplay layout 0.3.0 needs an Evac point.");
            GameObject evacPointGo = InstantiatePrefab("AI/Operations Evac Point.prefab");
            evacPointGo.name = "EvacPoint";
            evacPointGo.transform.position = Snap(layout.evac_m[0], layout.evac_m[1], "evac");
            var evacPoint = evacPointGo.GetComponent<OperationsEvac>();
            GameObject evacGo = InstantiatePrefab("Objectives/Obj_Op_Evac.prefab");
            var evac = evacGo.GetComponent<ObjectiveOpEvac>();
            evac.AttackingTeamSpawns = new SpawnSystem[0];
            evac.DefendingTeamSpawns = new[] { defenders };
            evac.AI_SpawnPoints = spawns;
            evac.PossibleEvacPoints = new List<OperationsEvac> { evacPoint };
            InstantiatePrefab("Game Modes/GM_Op_Evac.prefab").GetComponent<GM_Operations_Evac>().Objectives = new[] { evac };

            if (layout.harddrives_m == null || layout.harddrives_m.Length < 3)
                throw new InvalidOperationException("gameplay layout 0.3.0 needs 3 Retrieval hard-drive points.");
            var drives = new List<Vector3>();
            for (int i = 0; i < layout.harddrives_m.Length; i++)
                drives.Add(Snap(layout.harddrives_m[i].x, layout.harddrives_m[i].y, "hard drive " + i));
            var retrieval = InstantiatePrefab("Game Modes/GM_Op_Retrieval.prefab").GetComponent<GM_Operations_Retrieval>();
            retrieval.Objectives = new[] { hunt };
            retrieval.PossibleEvacPoints = new[] { evacPoint };
            retrieval.HarddriveSpawnLocations = drives;
            retrieval.NavmeshOriginPoint = Snap(layout.uplink_m[0], layout.uplink_m[1], "retrieval nav origin");
        }

        private static Transform ModeMarker(Transform parent, string name, float[] xy)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);
            go.transform.position = Local(xy[0], xy[1], 0f);
            return go.transform;
        }

        private static void PlaceLocalAudio(string objectName, string clip, float[] xy)
        {
            if (xy == null || xy.Length < 2) return;
            GameObject go = InstantiatePrefab("OnwardAudioSource.prefab");
            go.name = objectName;
            go.transform.position = Local(xy[0], xy[1], 2f);
            var source = go.GetComponent<OnwardAudioSource>();
            source.AudioSourceType = OnwardAudioType.LocalAmbience;
            source.AudioSourceName = clip;
        }

        // ---- Interior lights and light probes (ADR-013) ------------------------------------
        // Everything here is baked: no realtime light reaches the Quest. Warm white, below the sun (1.2).
        private static readonly Color InteriorLightColour = new Color(1f, 0.93f, 0.82f);
        private const float OutdoorProbeSpacingM = 12f;
        private const int OutdoorProbeColumnsMax = 1500;
        private static readonly float[] OutdoorProbeHeightsM = { 1f, 6f };

        // One baked point light per furnished room, in node order, up to interior_lights_max.
        // The probes of every room are collected even past the cap.
        private static int AddInteriorLights(Glb visual, Options opt, List<Vector3> probes)
        {
            var root = new GameObject("InteriorLights");
            int lights = 0, skipped = 0;
            foreach (GNode node in visual.Doc.nodes)
            {
                GRoomAnchor[] rooms = node.extras?.light_anchors?.rooms;
                if (rooms == null) continue;
                foreach (GRoomAnchor room in rooms)
                {
                    if (room == null) continue;
                    if (room.probes_m != null)
                    {
                        for (int i = 0; i + 2 < room.probes_m.Length; i += 3)
                            probes.Add(Local(room.probes_m[i], room.probes_m[i + 1], room.probes_m[i + 2]));
                    }
                    // detail leaves light_m out of a room past interiors.lights_max; the room keeps its probes.
                    if (room.light_m == null || room.light_m.Length != 3) continue;
                    if (lights >= opt.interior_lights_max)
                    {
                        skipped++;
                        continue;
                    }
                    var go = new GameObject("Light_" + (room.room_id ?? lights.ToString(CultureInfo.InvariantCulture)).Replace('/', '_'));
                    go.transform.SetParent(root.transform, false);
                    go.transform.position = Local(room.light_m[0], room.light_m[1], room.light_m[2]);
                    var light = go.AddComponent<Light>();
                    light.type = LightType.Point;
                    light.lightmapBakeType = LightmapBakeType.Baked;
                    light.color = InteriorLightColour;
                    light.intensity = opt.interior_light_intensity;
                    light.range = room.range_m;
                    // Baked shadows keep the light in its room: walls and slabs stop it.
                    light.shadows = LightShadows.Soft;
                    lights++;
                }
            }
            if (lights == 0) UnityEngine.Object.DestroyImmediate(root);
            Debug.Log($"{LogPrefix} Interior lights: {lights} baked, {skipped} over the cap of {opt.interior_lights_max}; {probes.Count} room probes.");
            return lights;
        }

        // Probes light every renderer kept out of the lightmap (props, trees, metal, the
        // backdrop) and the players. A group with only room probes would light the street
        // from inside the buildings, so it is added only with outdoor probes on the navmesh.
        private static int AddLightProbes(List<Vector3> roomProbes, bool hasNavMesh)
        {
            var points = new List<Vector3>(roomProbes);
            int columns = 0;
            NavMeshTriangulation tri = hasNavMesh ? NavMesh.CalculateTriangulation() : new NavMeshTriangulation();
            if (tri.vertices != null && tri.vertices.Length > 0)
            {
                var bounds = new Bounds(tri.vertices[0], Vector3.zero);
                foreach (Vector3 v in tri.vertices) bounds.Encapsulate(v);
                float step = Mathf.Max(OutdoorProbeSpacingM, Mathf.Sqrt(bounds.size.x * bounds.size.z / OutdoorProbeColumnsMax));
                int walkable = NavMesh.GetAreaFromName("Walkable");
                int mask = walkable >= 0 ? 1 << walkable : NavMesh.AllAreas;
                for (float x = bounds.min.x + step / 2f; x <= bounds.max.x; x += step)
                {
                    for (float z = bounds.min.z + step / 2f; z <= bounds.max.z; z += step)
                    {
                        NavMeshHit hit;
                        if (!NavMesh.SamplePosition(new Vector3(x, bounds.center.y, z), out hit, step / 2f, mask)) continue;
                        foreach (float h in OutdoorProbeHeightsM) points.Add(hit.position + Vector3.up * h);
                        columns++;
                    }
                }
            }
            if (columns == 0)
            {
                Debug.Log($"{LogPrefix} Light probes: no navmesh, none added; unlit renderers keep the ambient probe.");
                return 0;
            }
            var go = new GameObject("LightProbes");
            go.AddComponent<LightProbeGroup>().probePositions = points.ToArray();
            Debug.Log($"{LogPrefix} Light probes: {points.Count} ({roomProbes.Count} in rooms, {columns} outdoor columns).");
            return points.Count;
        }

        private static void Bake(string root, Options opt)
        {
            var settings = new LightingSettings { name = "Lighting" };
            AssetDatabase.CreateAsset(settings, $"{root}/Lighting.lighting");
            settings.bakedGI = true;
            settings.realtimeGI = false;
            settings.mixedBakeMode = MixedLightingMode.Subtractive;
            settings.lightmapper = LightingSettings.Lightmapper.ProgressiveGPU;
            settings.lightmapResolution = opt.lightmap_texels_per_m;
            settings.lightmapPadding = 2;
            settings.lightmapMaxSize = opt.lightmap_max_size;
            settings.directionalityMode = opt.lightmap_directional ? LightmapsMode.CombinedDirectional : LightmapsMode.NonDirectional;
            // Onward lightmapping guide (Aug 2023): lower end of the city range.
            // Occlusion culling and static batching stay off (ADR-005).
            settings.directSampleCount = 256;
            settings.indirectSampleCount = 512;
            settings.environmentSampleCount = 512;
            settings.minBounces = 2;
            settings.maxBounces = 4;
            settings.ao = true;
            settings.lightmapCompression = LightmapCompression.None;
            settings.filteringMode = LightingSettings.FilterMode.Advanced;
            settings.denoiserTypeDirect = LightingSettings.DenoiserType.OpenImage;
            settings.denoiserTypeIndirect = LightingSettings.DenoiserType.OpenImage;
            settings.denoiserTypeAO = LightingSettings.DenoiserType.OpenImage;
            EditorUtility.SetDirty(settings);
            Lightmapping.lightingSettings = settings;
            if (opt.occlusion_culling && !StaticOcclusionCulling.Compute())
                throw new InvalidOperationException("StaticOcclusionCulling.Compute() returned false.");
            if (!Lightmapping.Bake()) throw new InvalidOperationException("Lightmapping.Bake() returned false.");
            CompressLightmapsAstc();
            Debug.Log(string.Format(CultureInfo.InvariantCulture,
                "{0} Lightmaps: {1}, {2} texels, at {3} texels/m, at most {4} px.",
                LogPrefix, LightmapSettings.lightmaps.Length, LightmapTexels(), opt.lightmap_texels_per_m, opt.lightmap_max_size));
        }

        // Baked colour texels of every lightmap; compare with lightmap_estimate in onward_export.json.
        private static long LightmapTexels()
        {
            long texels = 0;
            foreach (LightmapData data in LightmapSettings.lightmaps)
                if (data.lightmapColor != null) texels += (long)data.lightmapColor.width * data.lightmapColor.height;
            return texels;
        }

        private static float ScaleInLightmap(Options opt, GMaterial[] materials, int index)
        {
            if (index < 0 || index >= materials.Length) return 1f;
            foreach (LightmapScale row in opt.lightmap_scales ?? new LightmapScale[0])
                if (row != null && row.material == materials[index].name) return row.scale;
            return 1f;
        }

        // The Quest build of Onward uses Vulkan. Auto Graphics API left GLES3 first.
        private static void ApplyQuestGraphics()
        {
            var apis = new[] { GraphicsDeviceType.Vulkan, GraphicsDeviceType.OpenGLES3 };
            GraphicsDeviceType[] current = PlayerSettings.GetGraphicsAPIs(BuildTarget.Android);
            bool matches = current.Length == apis.Length;
            for (int i = 0; matches && i < apis.Length; i++)
                matches = current[i] == apis[i];
            if (PlayerSettings.GetUseDefaultGraphicsAPIs(BuildTarget.Android) || !matches)
            {
                PlayerSettings.SetUseDefaultGraphicsAPIs(BuildTarget.Android, false);
                PlayerSettings.SetGraphicsAPIs(BuildTarget.Android, apis);
            }
        }

        private static void CompressLightmapsAstc()
        {
            var seen = new HashSet<string>();
            foreach (LightmapData data in LightmapSettings.lightmaps)
            {
                CompressAstc(data.lightmapColor, seen);
                CompressAstc(data.lightmapDir, seen);
                CompressAstc(data.shadowMask, seen);
            }
        }

        private static void CompressAstc(Texture texture, HashSet<string> seen)
        {
            if (texture == null) return;
            string path = AssetDatabase.GetAssetPath(texture);
            if (string.IsNullOrEmpty(path) || !seen.Add(path)) return;
            var importer = AssetImporter.GetAtPath(path) as TextureImporter;
            if (importer == null) return;
            var android = importer.GetPlatformTextureSettings("Android");
            android.overridden = true;
            android.format = TextureImporterFormat.ASTC_8x8;
            android.compressionQuality = 50;
            importer.SetPlatformTextureSettings(android);
            importer.SaveAndReimport();
        }

        // Editor-side figures only; device behaviour is measured on the Quest (docs/09-perf.md).
        private static string SceneReport(Package pkg, string scenePath, int visualObjects, int colliders, double buildSeconds, double bakeSeconds)
        {
            MeshRenderer[] renderers = UnityEngine.Object.FindObjectsOfType<MeshRenderer>();
            long tris = 0, verts = 0;
            var mats = new HashSet<Material>();
            foreach (MeshRenderer r in renderers)
            {
                Mesh m = r.GetComponent<MeshFilter>()?.sharedMesh;
                if (m == null) continue;
                foreach (Material mat in r.sharedMaterials) mats.Add(mat);
                for (int s = 0; s < m.subMeshCount; s++) tris += (long)m.GetIndexCount(s) / 3;
                verts += m.vertexCount;
            }

            MeshCollider[] meshColliders = UnityEngine.Object.FindObjectsOfType<MeshCollider>();
            long colliderTris = meshColliders.Where(c => c.sharedMesh != null).Sum(c => (long)c.sharedMesh.triangles.Length / 3);
            long renderersOnColliders = meshColliders.Count(c => c.GetComponent<Renderer>() != null);
            var sb = new StringBuilder();
            sb.Append("{\"schema\":\"onward_generator.onward_adapter_scene_report\",\"schema_version\":\"0.1.0\",");
            sb.Append("\"note\":\"Unity Editor statistics; not device measurements.\",");
            sb.Append($"\"adapter\":{{\"name\":\"{AdapterName}\",\"version\":\"{AdapterVersion}\"}},");
            sb.Append($"\"package_id\":\"{pkg.Manifest.package_id}\",\"manifest_sha256\":\"{pkg.ManifestSha256}\",");
            sb.Append($"\"unity\":\"{Application.unityVersion}\",\"scene\":\"{scenePath}\",");
            sb.Append($"\"options\":{JsonUtility.ToJson(pkg.Options)},");
            sb.Append($"\"visual_objects\":{visualObjects},\"mesh_renderers\":{renderers.Length},\"materials\":{mats.Count},");
            sb.Append($"\"triangles\":{tris},\"vertices\":{verts},");
            sb.Append($"\"mesh_colliders\":{colliders},\"collider_triangles\":{colliderTris},\"colliders_with_renderer\":{renderersOnColliders},");
            sb.Append($"\"lightmaps\":{LightmapSettings.lightmaps.Length},\"lightmap_texels\":{LightmapTexels()},");
            int probeCount = UnityEngine.Object.FindObjectsOfType<LightProbeGroup>().Sum(g => g.probePositions.Length);
            sb.Append($"\"lights\":{UnityEngine.Object.FindObjectsOfType<Light>().Length},\"light_probes\":{probeCount},");
            sb.Append(string.Format(CultureInfo.InvariantCulture, "\"seconds\":{{\"scene\":{0:F1},\"bake\":{1:F1}", buildSeconds, bakeSeconds));
            sb.Append("}}");
            return sb.ToString();
        }

        // ---- Helpers ---------------------------------------------------------------------------
        // Thin trees, lamps and benches baked black at 2 lightmap texels/m (Quest observation).
        // Overgrow props (material prop_<id>, overgrow/realize.py and dressing.py) are small
        // and often flush against a wall; air-conditioner units came out solid black on the
        // headset. They are lit by light probes or the ambient probe instead. The horizon
        // backdrop (ADR-014) is outside the play area and takes no lightmap either.
        private static bool ContributesGi(Material material)
        {
            string n = material != null ? material.name.ToLowerInvariant() : "";
            return !(n.Contains("tree_") || n.Contains("urban_metal") || n.Contains("railing_metal")
                || n.StartsWith("m_prop_") || n.Contains("horizon_backdrop"));
        }

        private static StaticEditorFlags LightmapFlags(StaticEditorFlags flags, IEnumerable<Material> mats)
        {
            if (mats.Any(m => !ContributesGi(m))) flags &= ~StaticEditorFlags.ContributeGI;
            return flags;
        }

        private static Material GetOrCreateMaterial(GMaterial g, string folder, Texture2D atlas = null)
        {
            string safe = new string(g.name.Select(ch => char.IsLetterOrDigit(ch) || ch == '_' ? ch : '_').ToArray());
            string path = $"{folder}/M_{safe}.mat";
            var existing = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (existing != null) return existing;
            if (safe == ViewGlassMaterial)
            {
                // Compiled shader already blends and draws both faces. Do not set
                // Surface, Cull, or _BaseColor: this graph does not expose them.
                var sprite = AssetDatabase.LoadAssetAtPath<Shader>(SpriteShaderPath)
                    ?? throw new InvalidOperationException($"Missing SDK shader at {SpriteShaderPath}.");
                var view = new Material(sprite) { name = $"M_{safe}" };
                if (atlas != null && view.HasProperty("_MainTex"))
                    view.SetTexture("_MainTex", atlas);
                view.enableInstancing = false;
                AssetDatabase.CreateAsset(view, path);
                return view;
            }
            var shader = AssetDatabase.LoadAssetAtPath<Shader>(LitShaderPath)
                ?? throw new InvalidOperationException($"Missing SDK shader at {LitShaderPath}.");
            var material = new Material(shader) { name = $"M_{safe}" };
            float[] c = g.pbrMetallicRoughness?.baseColorFactor;
            // glTF base colours are linear; Unity material colours are authored in sRGB.
            Color color = c != null && c.Length >= 3 ? new Color(c[0], c[1], c[2], 1f).gamma : Color.grey;
            if (!material.HasProperty("_BaseColor")) throw new InvalidOperationException($"{shader.name} has no _BaseColor property.");
            material.SetColor("_BaseColor", color);
            // The perimeter wall is a flat generated colour. The city atlas would
            // paint a random tile onto it because that mesh has no atlas UVs.
            bool solidColour = safe.IndexOf("envelope", StringComparison.OrdinalIgnoreCase) >= 0;
            if (atlas != null && !solidColour)
            {
                if (material.HasProperty("_BaseMap")) material.SetTexture("_BaseMap", atlas);
                if (material.HasProperty("_MainTex")) material.SetTexture("_MainTex", atlas);
            }
            else if (solidColour)
            {
                var solid = new Texture2D(4, 4, TextureFormat.RGB24, false);
                var pixels = new Color[16];
                for (int i = 0; i < pixels.Length; i++) pixels[i] = color;
                solid.SetPixels(pixels);
                solid.Apply(false, false);
                solid.name = "T_" + safe;
                AssetDatabase.CreateAsset(solid, $"{folder}/T_{safe}.asset");
                if (material.HasProperty("_BaseMap")) material.SetTexture("_BaseMap", solid);
                if (material.HasProperty("_MainTex")) material.SetTexture("_MainTex", solid);
                material.SetColor("_BaseColor", Color.white);
            }
            material.enableInstancing = false;
            AssetDatabase.CreateAsset(material, path);
            return material;
        }

        private static GameObject InstantiatePrefab(string relativePath)
        {
            string path = $"{PrefabRoot}/{relativePath}";
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(path)
                ?? throw new InvalidOperationException($"Missing SDK prefab: {path}");
            return (GameObject)PrefabUtility.InstantiatePrefab(prefab);
        }

        private const string ListingThumbnailAsset = "Assets/OnwardGenerator/Editor/Thumbnails/apocalipsis_murciana.png";

        private static Texture2D LoadListingThumbnail(Package pkg, string root)
        {
            PFile generated = pkg.Manifest.files.FirstOrDefault(f => f.role == "thumbnail");
            if (generated != null)
            {
                string file = Path.Combine(pkg.Dir, generated.path);
                var tex = new Texture2D(2, 2, TextureFormat.RGB24, false) { name = "ListingThumbnail" };
                if (!tex.LoadImage(File.ReadAllBytes(file)))
                    throw new InvalidOperationException($"thumbnail {file} is not a readable image");
                EnsureFolder(root + "/Textures");
                AssetDatabase.CreateAsset(tex, $"{root}/Textures/ListingThumbnail.asset");
                return tex;
            }
            var importer = AssetImporter.GetAtPath(ListingThumbnailAsset) as TextureImporter;
            if (importer != null)
            {
                importer.isReadable = true;
                importer.npotScale = TextureImporterNPOTScale.None;
                importer.textureCompression = TextureImporterCompression.Uncompressed;
                importer.maxTextureSize = 512;
                importer.mipmapEnabled = false;
                AssetDatabase.ImportAsset(ListingThumbnailAsset);
            }
            return AssetDatabase.LoadAssetAtPath<Texture2D>(ListingThumbnailAsset);
        }

        private static void EnsureFolder(string assetPath)
        {
            if (AssetDatabase.IsValidFolder(assetPath)) return;
            string parent = Path.GetDirectoryName(assetPath)?.Replace('\\', '/');
            if (!string.IsNullOrEmpty(parent) && !AssetDatabase.IsValidFolder(parent)) EnsureFolder(parent);
            AssetDatabase.CreateFolder(parent, Path.GetFileName(assetPath));
        }

        private static string RequireArg(string flag)
        {
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == flag) return args[i + 1];
            }

            throw new ArgumentException($"missing command-line argument {flag}");
        }

        private static string OptionalArg(string flag)
        {
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == flag) return args[i + 1];
            }
            return null;
        }

        private static void RunGuarded(Action action)
        {
            try
            {
                action();
                Debug.Log($"{LogPrefix} OK");
                if (Application.isBatchMode) EditorApplication.Exit(0);
            }
            catch (Exception e)
            {
                Debug.LogError($"{LogPrefix} FAILED: {e.GetType().Name}: {e.Message}\n{e.StackTrace}");
                if (Application.isBatchMode) EditorApplication.Exit(1);
                else throw;
            }
        }
    }
}
#endif
