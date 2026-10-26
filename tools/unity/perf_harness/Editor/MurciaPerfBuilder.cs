#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using Onward.CustomContent;
using Onward.CustomContent.Assets;
using Onward.CustomContent.EditorTools;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Profiling;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using Debug = UnityEngine.Debug;

namespace MurciaPerf
{
    // Phase 05 device-measurement harness. Loads the generator's GLB files (only the
    // glTF subset onward-generator writes: POSITION, NORMAL, uint32/uint16 indices,
    // node matrices, baseColorFactor) into a scene with the SDK prefabs required by
    // the Onward validator, bakes lighting and builds with the SDK's Onward Launcher.
    // It is a measurement tool, not the final export adapter.
    public static class MurciaPerfBuilder
    {
        private const string LogPrefix = "[MurciaPerf]";
        private const string SdkRoot = "Assets/OnwardCustomContent";
        private const string PrefabRoot = SdkRoot + "/Prefabs";
        private const string LitShaderPath = SdkRoot + "/Internal/Shaders/PP_Opaque_Simple_Lit.shadergraph";
        private const string WorkRoot = "Assets/MurciaPerf";
        private const string MaterialRoot = WorkRoot + "/Materials";
        private const string LauncherBinPathPref = "_ONWARD_BIN_PATH_";
        private const string LauncherPlatformPref = "_ONWARD_PLATFORM_";
        private const int LauncherPlatformQuest = 1;
        private const float SpawnHeight = 0.05f;

        [Serializable]
        private class Options
        {
            public string variant = "";
            public bool static_batching = false;
            public bool occlusion_culling = false;
            public float lightmap_texels_per_m = 2f;
            public int lightmap_max_size = 2048;
            public bool lightmap_directional = true;
            public float occlusion_smallest_occluder_m = 5f;
            public float occlusion_smallest_hole_m = 0.25f;
            // > 0: visual primitives are merged per (ground cell of this size, material).
            public float combine_cell_m = 0f;
        }

        [Serializable] private class XY { public float x; public float y; }
        [Serializable] private class Block { public float[] centre_m; public XY[] points_m; }
        [Serializable] private class Damage { public float z_m; public float[] size_m; public float[] centre_m; }

        [Serializable]
        private class Layout
        {
            public string schema;
            public Block attackers;
            public Block defenders;
            public float[] uplink_m;
            public float[] map_quad_m;
            public float level_size_m;
            public float tent_z_m;
            public Damage damage_volume;
        }

        // ---- glTF subset -------------------------------------------------------------
        [Serializable] private class GAttributes { public int POSITION = -1; public int NORMAL = -1; }
        [Serializable] private class GPrimitive { public GAttributes attributes; public int indices = -1; public int material = -1; public int mode = 4; }
        [Serializable] private class GMesh { public string name; public GPrimitive[] primitives; }
        [Serializable] private class GNode { public string name; public int mesh = -1; public float[] matrix; }
        [Serializable] private class GAccessor { public int bufferView = -1; public int byteOffset; public int componentType; public int count; public string type; }
        [Serializable] private class GView { public int buffer; public int byteOffset; public int byteLength; }
        [Serializable] private class GPbr { public float[] baseColorFactor; }
        [Serializable] private class GMaterial { public string name; public GPbr pbrMetallicRoughness; }
        [Serializable] private class Gltf { public GNode[] nodes; public GMesh[] meshes; public GAccessor[] accessors; public GView[] bufferViews; public GMaterial[] materials; }

        private sealed class Glb
        {
            public Gltf Doc;
            public byte[] Bin;
        }

        private static Glb ReadGlb(string path)
        {
            byte[] data = File.ReadAllBytes(path);
            if (data.Length < 28 || Encoding.ASCII.GetString(data, 0, 4) != "glTF" || BitConverter.ToUInt32(data, 4) != 2)
            {
                throw new InvalidDataException($"{path}: not a glTF 2.0 binary");
            }

            int jsonLength = (int)BitConverter.ToUInt32(data, 12);
            if (Encoding.ASCII.GetString(data, 16, 4) != "JSON")
            {
                throw new InvalidDataException($"{path}: first chunk is not JSON");
            }

            string json = Encoding.UTF8.GetString(data, 20, jsonLength);
            int binHeader = 20 + jsonLength;
            int binLength = (int)BitConverter.ToUInt32(data, binHeader);
            if (Encoding.ASCII.GetString(data, binHeader + 4, 4) != "BIN\0")
            {
                throw new InvalidDataException($"{path}: second chunk is not BIN");
            }

            var bin = new byte[binLength];
            Buffer.BlockCopy(data, binHeader + 8, bin, 0, binLength);
            return new Glb { Doc = JsonUtility.FromJson<Gltf>(json), Bin = bin };
        }

        private static Vector3[] ReadVec3(Glb glb, int accessorIndex)
        {
            GAccessor a = glb.Doc.accessors[accessorIndex];
            if (a.componentType != 5126 || a.type != "VEC3")
            {
                throw new InvalidDataException($"accessor {accessorIndex}: expected float VEC3");
            }

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
                for (int i = 0; i < a.count; i++)
                {
                    result[i] = BitConverter.ToUInt16(glb.Bin, offset + 2 * i);
                }
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
            var submeshes = new List<int[]>();
            var mats = new List<int>();
            foreach (GPrimitive prim in gmesh.primitives)
            {
                if (prim.mode != 4)
                {
                    throw new InvalidDataException($"{name}: only triangle primitives are supported");
                }

                int baseVertex = positions.Count;
                positions.AddRange(ReadVec3(glb, prim.attributes.POSITION));
                if (prim.attributes.NORMAL >= 0)
                {
                    normals.AddRange(ReadVec3(glb, prim.attributes.NORMAL));
                }

                int[] idx = ReadIndices(glb, prim.indices);
                for (int i = 0; i < idx.Length; i++)
                {
                    idx[i] += baseVertex;
                }

                submeshes.Add(idx);
                mats.Add(prim.material);
            }

            var mesh = new Mesh { name = name };
            mesh.indexFormat = positions.Count > 65535 ? IndexFormat.UInt32 : IndexFormat.UInt16;
            mesh.SetVertices(positions);
            if (normals.Count == positions.Count)
            {
                mesh.SetNormals(normals);
            }

            mesh.subMeshCount = submeshes.Count;
            for (int s = 0; s < submeshes.Count; s++)
            {
                mesh.SetTriangles(submeshes[s], s, false);
            }

            mesh.RecalculateBounds();
            materialIndices = mats.ToArray();
            return mesh;
        }

        private static void ApplyMatrix(Transform t, float[] m)
        {
            if (m == null || m.Length != 16)
            {
                return;
            }

            var g = new Matrix4x4();
            for (int c = 0; c < 4; c++)
            {
                for (int r = 0; r < 4; r++)
                {
                    g[r, c] = m[c * 4 + r];
                }
            }

            float[] s = { 1f, 1f, -1f, 1f };
            var u = new Matrix4x4();
            for (int c = 0; c < 4; c++)
            {
                for (int r = 0; r < 4; r++)
                {
                    u[r, c] = g[r, c] * s[r] * s[c];
                }
            }

            Vector3 x = u.GetColumn(0), y = u.GetColumn(1), z = u.GetColumn(2);
            t.localPosition = u.GetColumn(3);
            t.localRotation = Quaternion.LookRotation(z.normalized, y.normalized);
            t.localScale = new Vector3(x.magnitude, y.magnitude, z.magnitude);
        }

        // Merges every visual primitive into one mesh per (ground cell, material). A node
        // goes to the cell holding its bounds centre, on glTF (x, z), the same rule as
        // onward_generator.perf.measure.combine_groups. Instanced meshes are copied, so
        // each merged mesh gets its own lightmap UVs. Collision is not touched.
        private static void BuildCombinedVisual(Glb visual, Mesh[] meshes, int[][] meshMaterials, Material[] materials,
            Transform parent, string root, float cellM, StaticEditorFlags flags)
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
                        if (!groups.TryGetValue(key, out List<CombineInstance> list))
                        {
                            groups[key] = list = new List<CombineInstance>();
                        }

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
                    long verts = g.Value.Sum(ci => (long)ci.mesh.GetSubMesh(ci.subMeshIndex).vertexCount);
                    var combined = new Mesh { name = name, indexFormat = verts > 65535 ? IndexFormat.UInt32 : IndexFormat.UInt16 };
                    combined.CombineMeshes(g.Value.ToArray(), true, true, false);
                    combined.RecalculateBounds();
                    Unwrapping.GenerateSecondaryUVSet(combined);
                    AssetDatabase.CreateAsset(combined, $"{root}/Meshes/{name}.asset");
                    built.Add((name, combined, mat));
                }
            }
            finally
            {
                AssetDatabase.StopAssetEditing();
            }

            foreach ((string name, Mesh mesh, int mat) in built)
            {
                var go = new GameObject(name);
                go.transform.SetParent(parent, false);
                go.AddComponent<MeshFilter>().sharedMesh = mesh;
                var renderer = go.AddComponent<MeshRenderer>();
                renderer.sharedMaterial = materials[mat];
                renderer.shadowCastingMode = ShadowCastingMode.On;
                GameObjectUtility.SetStaticEditorFlags(go, flags);
            }

            Debug.Log($"{LogPrefix} Combined visual: {built.Count} meshes from {groups.Values.Sum(l => l.Count)} primitives, cell {cellM} m");
        }

        // ---- Entry points --------------------------------------------------------------
        [MenuItem("ONWARD MurciaPerf/1. Build measurement scene")]
        public static void BuildScene()
        {
            RunGuarded(() =>
            {
                var clock = Stopwatch.StartNew();
                string sceneDir = RequireArg("-perfScene");
                Options opt = JsonUtility.FromJson<Options>(File.ReadAllText(RequireArg("-perfOptions")));
                Layout layout = JsonUtility.FromJson<Layout>(File.ReadAllText(RequireArg("-perfLayout")));
                string statsOut = RequireArg("-perfStats");
                if (string.IsNullOrEmpty(opt.variant) || opt.variant.IndexOfAny(new[] { '/', '\\', '.', ' ' }) >= 0)
                {
                    throw new ArgumentException("options.variant must be a plain identifier");
                }

                if (layout.schema != "onward_generator.perf_layout")
                {
                    throw new ArgumentException("layout is not a onward_generator.perf_layout document");
                }

                string root = $"{WorkRoot}/{opt.variant}";
                EnsureFolder(root + "/Meshes");
                EnsureFolder(MaterialRoot);
                string scenePath = $"{root}/Murcia_{opt.variant}.unity";

                Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                StaticEditorFlags flags = StaticEditorFlags.ContributeGI;
                if (opt.static_batching) flags |= StaticEditorFlags.BatchingStatic;
                if (opt.occlusion_culling) flags |= StaticEditorFlags.OccluderStatic | StaticEditorFlags.OccludeeStatic;

                var visualRoot = new GameObject("Visual");
                Glb visual = ReadGlb(Path.Combine(sceneDir, "scene_visual.glb"));
                Material[] materials = visual.Doc.materials.Select(GetOrCreateMaterial).ToArray();
                var visualMeshes = new Mesh[visual.Doc.meshes.Length];
                var visualMaterials = new int[visual.Doc.meshes.Length][];
                AssetDatabase.StartAssetEditing();
                try
                {
                    for (int i = 0; i < visual.Doc.meshes.Length; i++)
                    {
                        Mesh mesh = BuildMesh(visual, visual.Doc.meshes[i], $"V{i:D4}", out visualMaterials[i]);
                        if (opt.combine_cell_m > 0)
                        {
                            visualMeshes[i] = mesh;
                            continue;
                        }

                        Unwrapping.GenerateSecondaryUVSet(mesh);
                        AssetDatabase.CreateAsset(mesh, $"{root}/Meshes/V{i:D4}.asset");
                        visualMeshes[i] = mesh;
                    }
                }
                finally
                {
                    AssetDatabase.StopAssetEditing();
                }

                if (opt.combine_cell_m > 0)
                {
                    BuildCombinedVisual(visual, visualMeshes, visualMaterials, materials, visualRoot.transform, root, opt.combine_cell_m, flags);
                }
                else
                {
                    foreach (GNode node in visual.Doc.nodes.Where(n => n.mesh >= 0))
                    {
                        var go = new GameObject(node.name.Replace('/', '_'));
                        go.transform.SetParent(visualRoot.transform, false);
                        ApplyMatrix(go.transform, node.matrix);
                        go.AddComponent<MeshFilter>().sharedMesh = visualMeshes[node.mesh];
                        var renderer = go.AddComponent<MeshRenderer>();
                        renderer.sharedMaterials = visualMaterials[node.mesh].Select(k => materials[k]).ToArray();
                        renderer.shadowCastingMode = ShadowCastingMode.On;
                        GameObjectUtility.SetStaticEditorFlags(go, flags);
                    }
                }

                var collisionRoot = new GameObject("Collision");
                Glb collision = ReadGlb(Path.Combine(sceneDir, "scene_collision.glb"));
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

                foreach (GNode node in collision.Doc.nodes.Where(n => n.mesh >= 0))
                {
                    var go = new GameObject(node.name.Replace('/', '_'));
                    go.transform.SetParent(collisionRoot.transform, false);
                    ApplyMatrix(go.transform, node.matrix);
                    go.AddComponent<MeshCollider>().sharedMesh = collisionMeshes[node.mesh];
                }

                var physic = collisionRoot.AddComponent<SetPhysicMaterial>();
                physic.MaterialType = SetPhysicMaterial.EPhysicMaterialType.Concrete;
                physic.IncludeChildren = true;

                var sun = new GameObject("Directional Light");
                var light = sun.AddComponent<Light>();
                light.type = LightType.Directional;
                light.lightmapBakeType = LightmapBakeType.Baked;
                light.intensity = 1.2f;
                light.shadows = LightShadows.Soft;
                sun.transform.rotation = Quaternion.Euler(50f, -30f, 0f);

                AddGameplay(layout);
                EditorSceneManager.SaveScene(scene, scenePath);

                string sharedPath = $"{root}/SharedLevelSettings_{opt.variant}.asset";
                var shared = AssetDatabase.LoadAssetAtPath<SharedLevelSettings>(sharedPath);
                if (shared == null)
                {
                    shared = ScriptableObject.CreateInstance<SharedLevelSettings>();
                    AssetDatabase.CreateAsset(shared, sharedPath);
                }

                shared.Scene_PC = null;
                shared.Scene_Quest = AssetDatabase.LoadAssetAtPath<SceneAsset>(scenePath);
                shared.UseBuiltInSkybox = true;
                EditorUtility.SetDirty(shared);

                GameObject descriptorObject = InstantiatePrefab("LevelDescriptor.prefab");
                var descriptor = descriptorObject.GetComponent<LevelDescriptor>();
                descriptor.SharedSettings = shared;
                descriptor.Title = $"Murcia {opt.variant}";
                descriptor.Description = "Phase 05 device measurement of the generated Murcia scene. Generic facades; not a faithful reconstruction.";
                EditorUtility.SetDirty(descriptor);

                double buildSeconds = clock.Elapsed.TotalSeconds;
                var bakeClock = Stopwatch.StartNew();
                Bake(root, opt);
                double bakeSeconds = bakeClock.Elapsed.TotalSeconds;

                double occlusionSeconds = 0;
                if (opt.occlusion_culling)
                {
                    var occClock = Stopwatch.StartNew();
                    StaticOcclusionCulling.smallestOccluder = opt.occlusion_smallest_occluder_m;
                    StaticOcclusionCulling.smallestHole = opt.occlusion_smallest_hole_m;
                    if (!StaticOcclusionCulling.Compute())
                    {
                        throw new InvalidOperationException("StaticOcclusionCulling.Compute() returned false.");
                    }

                    occlusionSeconds = occClock.Elapsed.TotalSeconds;
                }

                EditorSceneManager.MarkSceneDirty(scene);
                EditorSceneManager.SaveScene(scene, scenePath);
                AssetDatabase.SaveAssets();

                File.WriteAllText(statsOut, SceneStats(opt, scenePath, buildSeconds, bakeSeconds, occlusionSeconds));
                Debug.Log($"{LogPrefix} Scene saved: {scenePath}. Lightmaps: {LightmapSettings.lightmaps.Length}. Stats: {statsOut}");
            });
        }

        [MenuItem("ONWARD MurciaPerf/2. Build for Quest via Onward Launcher")]
        public static void BuildQuestViaLauncher()
        {
            RunGuarded(() =>
            {
                Options opt = JsonUtility.FromJson<Options>(File.ReadAllText(RequireArg("-perfOptions")));
                string outDir = RequireArg("-perfOut");
                Directory.CreateDirectory(outDir);
                string scenePath = $"{WorkRoot}/{opt.variant}/Murcia_{opt.variant}.unity";
                if (AssetDatabase.LoadAssetAtPath<SceneAsset>(scenePath) == null)
                {
                    throw new FileNotFoundException($"Scene {scenePath} not found; run the scene step first.");
                }

                EditorPrefs.SetString(LauncherBinPathPref, outDir);
                EditorPrefs.SetInt(LauncherPlatformPref, LauncherPlatformQuest);
                EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);

                MethodInfo build = typeof(OnwardLauncher).GetMethod("BuildForPlatform", BindingFlags.Instance | BindingFlags.NonPublic)
                    ?? throw new MissingMethodException("OnwardLauncher.BuildForPlatform not found. The SDK version may differ from v1.15.");
                var launcher = ScriptableObject.CreateInstance<OnwardLauncher>();
                var clock = Stopwatch.StartNew();
                try
                {
                    build.Invoke(launcher, new object[] { BuildTarget.Android, $"Murcia {opt.variant} Quest build finished." });
                }
                catch (TargetInvocationException e)
                {
                    throw e.InnerException ?? e;
                }
                finally
                {
                    UnityEngine.Object.DestroyImmediate(launcher);
                }

                string content = Path.Combine(outDir, $"unity-export.local.{CustomContentUtils.CONTENT_EXTENSION}");
                string info = Path.Combine(outDir, $"unity-export.local.{CustomContentUtils.INFO_EXTENSION}");
                if (!File.Exists(content) || !File.Exists(info))
                {
                    throw new FileNotFoundException($"Launcher did not produce both files in {outDir}.");
                }

                Debug.Log($"{LogPrefix} Launcher output OK in {clock.Elapsed.TotalSeconds:F1} s: {content} ({new FileInfo(content).Length} bytes)");
            });
        }

        // ---- Scene pieces ---------------------------------------------------------------
        private static Vector3 Local(float x, float y, float z) => new Vector3(x, z, y);

        private static void AddGameplay(Layout layout)
        {
            GameObject tent = InstantiatePrefab("Tent.prefab");
            tent.transform.position = Local(layout.attackers.centre_m[0], layout.attackers.centre_m[1], layout.tent_z_m);

            GameObject mapQuad = InstantiatePrefab("Map Quad.prefab");
            mapQuad.transform.position = Local(layout.map_quad_m[0], layout.map_quad_m[1], 0f);
            mapQuad.transform.rotation = Quaternion.identity;
            mapQuad.GetComponent<MapQuad>().LevelSize = layout.level_size_m;

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
        }

        private static SpawnSystem CreateSpawnSystem(string name, Block block)
        {
            GameObject go = InstantiatePrefab("SpawnSystem.prefab");
            go.name = name;
            Vector3 centre = Local(block.centre_m[0], block.centre_m[1], SpawnHeight);
            go.transform.position = centre;
            var system = go.GetComponent<SpawnSystem>();
            Spawnpoint[] points = system.AllSpawns;
            if (points.Length < 5 || points.Length > block.points_m.Length)
            {
                throw new InvalidOperationException($"{name}: prefab has {points.Length} spawn points; layout has {block.points_m.Length}.");
            }

            for (int i = 0; i < points.Length; i++)
            {
                points[i].transform.position = Local(block.points_m[i].x, block.points_m[i].y, SpawnHeight);
                points[i].transform.rotation = Quaternion.identity;
            }

            return system;
        }

        private static void Bake(string root, Options opt)
        {
            string path = $"{root}/Lighting_{opt.variant}.lighting";
            var settings = AssetDatabase.LoadAssetAtPath<LightingSettings>(path);
            if (settings == null)
            {
                settings = new LightingSettings { name = $"Lighting_{opt.variant}" };
                AssetDatabase.CreateAsset(settings, path);
            }

            settings.bakedGI = true;
            settings.realtimeGI = false;
            settings.mixedBakeMode = MixedLightingMode.Subtractive;
            settings.lightmapper = LightingSettings.Lightmapper.ProgressiveGPU;
            settings.lightmapResolution = opt.lightmap_texels_per_m;
            settings.lightmapPadding = 2;
            settings.lightmapMaxSize = opt.lightmap_max_size;
            settings.directionalityMode = opt.lightmap_directional ? LightmapsMode.CombinedDirectional : LightmapsMode.NonDirectional;
            settings.directSampleCount = 16;
            settings.indirectSampleCount = 64;
            settings.environmentSampleCount = 64;
            settings.maxBounces = 1;
            settings.filteringMode = LightingSettings.FilterMode.Auto;
            EditorUtility.SetDirty(settings);
            Lightmapping.lightingSettings = settings;
            if (!Lightmapping.Bake())
            {
                throw new InvalidOperationException("Lightmapping.Bake() returned false.");
            }
        }

        // ---- Editor-side statistics (not a substitute for device measurement) ---------
        private static string SceneStats(Options opt, string scenePath, double buildSeconds, double bakeSeconds, double occlusionSeconds)
        {
            MeshRenderer[] renderers = UnityEngine.Object.FindObjectsOfType<MeshRenderer>();
            var meshes = new HashSet<Mesh>();
            var mats = new HashSet<Material>();
            long tris = 0, verts = 0, submeshes = 0;
            int lightmapped = 0;
            foreach (MeshRenderer r in renderers)
            {
                Mesh m = r.GetComponent<MeshFilter>()?.sharedMesh;
                if (m == null) continue;
                meshes.Add(m);
                foreach (Material mat in r.sharedMaterials) mats.Add(mat);
                for (int s = 0; s < m.subMeshCount; s++) tris += (long)m.GetIndexCount(s) / 3;
                verts += m.vertexCount;
                submeshes += m.subMeshCount;
                if (r.lightmapIndex >= 0 && r.lightmapIndex < 65534) lightmapped++;
            }

            MeshCollider[] colliders = UnityEngine.Object.FindObjectsOfType<MeshCollider>();
            long colliderTris = colliders.Where(c => c.sharedMesh != null).Sum(c => (long)c.sharedMesh.triangles.Length / 3);
            Light[] lights = UnityEngine.Object.FindObjectsOfType<Light>();

            var sb = new StringBuilder();
            sb.Append("{\"schema\":\"onward_generator.perf_unity_stats\",\"schema_version\":\"0.1.0\",");
            sb.Append("\"note\":\"Unity Editor statistics for the saved scene. Not device measurements.\",");
            sb.Append($"\"unity\":\"{Application.unityVersion}\",\"variant\":\"{opt.variant}\",\"scene\":\"{scenePath}\",");
            sb.Append($"\"options\":{JsonUtility.ToJson(opt)},");
            sb.Append($"\"mesh_renderers\":{renderers.Length},\"submeshes\":{submeshes},\"unique_meshes\":{meshes.Count},");
            sb.Append($"\"materials\":{mats.Count},\"shaders\":{mats.Where(m => m != null).Select(m => m.shader).Distinct().Count()},");
            sb.Append($"\"triangles\":{tris},\"vertices\":{verts},\"lightmapped_renderers\":{lightmapped},");
            sb.Append($"\"lod_groups\":{UnityEngine.Object.FindObjectsOfType<LODGroup>().Length},");
            sb.Append($"\"mesh_colliders\":{colliders.Length},\"collider_triangles\":{colliderTris},");
            sb.Append($"\"box_colliders\":{UnityEngine.Object.FindObjectsOfType<BoxCollider>().Length},");
            sb.Append($"\"lights\":[{string.Join(",", lights.Select(l => $"{{\"type\":\"{l.type}\",\"bake\":\"{l.lightmapBakeType}\",\"shadows\":\"{l.shadows}\"}}"))}],");
            sb.Append($"\"occlusion_data_bytes\":{OcclusionDataBytes(scenePath)},");
            sb.Append("\"lightmaps\":[");
            var entries = new List<string>();
            foreach (LightmapData lm in LightmapSettings.lightmaps)
            {
                foreach ((string kind, Texture2D tex) in new[] { ("color", lm.lightmapColor), ("dir", lm.lightmapDir), ("shadowmask", lm.shadowMask) })
                {
                    if (tex == null) continue;
                    entries.Add($"{{\"kind\":\"{kind}\",\"width\":{tex.width},\"height\":{tex.height},\"format\":\"{tex.format}\",\"mips\":{tex.mipmapCount},\"runtime_bytes_editor\":{Profiler.GetRuntimeMemorySizeLong(tex)}}}");
                }
            }

            sb.Append(string.Join(",", entries));
            sb.Append("],");
            // Closing braces are appended separately: "}}" right after a format specifier is parsed as part of it.
            sb.Append(string.Format(CultureInfo.InvariantCulture, "\"seconds\":{{\"scene\":{0:F1},\"bake\":{1:F1},\"occlusion\":{2:F1}",
                buildSeconds, bakeSeconds, occlusionSeconds));
            sb.Append("}}");
            return sb.ToString();
        }

        // StaticOcclusionCulling.umbraDataSize reads 0 here even when the bake wrote data,
        // so the size is taken from the saved OcclusionCullingData asset; -1 means no asset.
        private static long OcclusionDataBytes(string scenePath)
        {
            string dir = Path.Combine(Path.GetDirectoryName(scenePath), Path.GetFileNameWithoutExtension(scenePath));
            UnityEngine.Object data = AssetDatabase.LoadMainAssetAtPath($"{dir}/OcclusionCullingData.asset");
            if (data == null) return -1;
            SerializedProperty pvs = new SerializedObject(data).FindProperty("m_PVSData");
            return pvs == null ? -1 : pvs.arraySize;
        }

        // ---- Helpers ----------------------------------------------------------------------
        private static Material GetOrCreateMaterial(GMaterial g)
        {
            string safe = new string(g.name.Select(ch => char.IsLetterOrDigit(ch) || ch == '_' ? ch : '_').ToArray());
            string path = $"{MaterialRoot}/M_{safe}.mat";
            var existing = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (existing != null)
            {
                return existing;
            }

            var shader = AssetDatabase.LoadAssetAtPath<Shader>(LitShaderPath)
                ?? throw new InvalidOperationException($"Missing SDK shader at {LitShaderPath}.");
            var material = new Material(shader) { name = $"M_{safe}" };
            float[] c = g.pbrMetallicRoughness?.baseColorFactor;
            // glTF base colours are linear; Unity material colours are authored in sRGB.
            Color color = c != null && c.Length >= 3 ? new Color(c[0], c[1], c[2], 1f).gamma : Color.grey;
            if (!material.HasProperty("_BaseColor"))
            {
                throw new InvalidOperationException($"{shader.name} has no _BaseColor property.");
            }

            material.SetColor("_BaseColor", color);
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

        private static void EnsureFolder(string assetPath)
        {
            if (AssetDatabase.IsValidFolder(assetPath))
            {
                return;
            }

            string parent = Path.GetDirectoryName(assetPath)?.Replace('\\', '/');
            if (!string.IsNullOrEmpty(parent) && !AssetDatabase.IsValidFolder(parent))
            {
                EnsureFolder(parent);
            }

            AssetDatabase.CreateFolder(parent, Path.GetFileName(assetPath));
        }

        private static string RequireArg(string flag)
        {
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == flag)
                {
                    return args[i + 1];
                }
            }

            throw new ArgumentException($"missing command-line argument {flag}");
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
