#if UNITY_EDITOR
using System;
using System.IO;
using System.Reflection;
using Onward.CustomContent;
using Onward.CustomContent.Assets;
using Onward.CustomContent.EditorTools;
using UnityEditor;
using UnityEditor.PackageManager;
using UnityEditor.PackageManager.Requests;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

namespace OnwardPhase0
{
    // Phase 0 compatibility probe. Builds a minimal room with the official
    // Onward Custom Content SDK prefabs and packages it with the SDK's own
    // Onward Launcher code path. It does not reimplement the map format.
    public static class Phase0RoomBuilder
    {
        private const string LogPrefix = "[Phase0]";

        private const string SdkRoot = "Assets/OnwardCustomContent";
        private const string PrefabRoot = SdkRoot + "/Prefabs";
        private const string PipelineAssetPath = SdkRoot + "/UniversalRenderPipelineAsset.asset";
        private const string LitShaderPath = SdkRoot + "/Internal/Shaders/PP_Opaque_Simple_Lit.shadergraph";

        private const string WorkRoot = "Assets/Phase0";
        private const string ScenePath = WorkRoot + "/Phase0_Room.unity";
        private const string SharedSettingsPath = WorkRoot + "/SharedLevelSettings_Phase0_Room.asset";
        private const string LightingSettingsPath = WorkRoot + "/Phase0_Room.lighting";
        private const string CubeMeshPath = WorkRoot + "/Meshes/Phase0_Cube_UV2.asset";
        private const string FloorMaterialPath = WorkRoot + "/Materials/M_Phase0_Floor.mat";
        private const string WallMaterialPath = WorkRoot + "/Materials/M_Phase0_Wall.mat";

        private const string LauncherBinPathPref = "_ONWARD_BIN_PATH_";
        private const string LauncherPlatformPref = "_ONWARD_PLATFORM_";
        private const int LauncherPlatformQuest = 1;

        // Room dimensions in metres (Unity units).
        private const float InteriorSize = 12f;
        private const float WallHeight = 3f;
        private const float WallThickness = 0.3f;
        private const float FloorThickness = 0.2f;
        private const float DoorWidth = 1.2f;
        private const float DoorHeight = 2.2f;

        private static readonly string[] XrPackages = { "com.unity.xr.management", "com.unity.xr.oculus" };

        // Adding a package triggers a domain reload, which drops editor update
        // callbacks. A single blocking request avoids depending on them.
        [MenuItem("ONWARD Phase0/1. Install XR packages")]
        public static void InstallXrPackages()
        {
            RunGuarded(() =>
            {
                Debug.Log($"{LogPrefix} Adding packages: {string.Join(", ", XrPackages)}");
                AddAndRemoveRequest request = Client.AddAndRemove(XrPackages, null);

                DateTime deadline = DateTime.UtcNow.AddMinutes(10);
                while (!request.IsCompleted)
                {
                    if (DateTime.UtcNow > deadline)
                    {
                        throw new TimeoutException("Package Manager did not finish adding XR packages within 10 minutes.");
                    }

                    System.Threading.Thread.Sleep(200);
                }

                if (request.Status != StatusCode.Success)
                {
                    throw new InvalidOperationException($"Package add failed: {request.Error?.message}");
                }

                foreach (var package in request.Result)
                {
                    if (Array.IndexOf(XrPackages, package.name) >= 0)
                    {
                        Debug.Log($"{LogPrefix} Resolved {package.packageId}");
                    }
                }
            });
        }

        [MenuItem("ONWARD Phase0/2. Configure project (guide settings)")]
        public static void ConfigureProject()
        {
            RunGuarded(() =>
            {
                var pipeline = AssetDatabase.LoadAssetAtPath<RenderPipelineAsset>(PipelineAssetPath);
                if (pipeline == null)
                {
                    throw new InvalidOperationException($"Missing SDK pipeline asset at {PipelineAssetPath}. Import the SDK package first.");
                }

                GraphicsSettings.renderPipelineAsset = pipeline;
                Debug.Log($"{LogPrefix} Graphics render pipeline = {PipelineAssetPath}");

                for (int i = 0; i < QualitySettings.names.Length; i++)
                {
                    QualitySettings.SetQualityLevel(i, false);
                    string qualityPipeline = QualitySettings.renderPipeline != null
                        ? AssetDatabase.GetAssetPath(QualitySettings.renderPipeline)
                        : "(none, uses Graphics setting)";
                    Debug.Log($"{LogPrefix} Quality level '{QualitySettings.names[i]}' pipeline: {qualityPipeline}");
                }

                ConfigureShaderStripping();

                PlayerSettings.SetScriptingBackend(BuildTargetGroup.Android, ScriptingImplementation.IL2CPP);
                PlayerSettings.SetScriptingBackend(BuildTargetGroup.Standalone, ScriptingImplementation.IL2CPP);
                PlayerSettings.Android.targetArchitectures = AndroidArchitecture.ARM64;
                EditorUserBuildSettings.selectedBuildTargetGroup = BuildTargetGroup.Android;

                if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.Android)
                {
                    if (!EditorUserBuildSettings.SwitchActiveBuildTarget(BuildTargetGroup.Android, BuildTarget.Android))
                    {
                        throw new InvalidOperationException("Could not switch active build target to Android. Is Android Build Support installed?");
                    }
                }

                AssetDatabase.SaveAssets();
                Debug.Log($"{LogPrefix} Project configured: Android active, IL2CPP, ARM64, shader stripping custom.");
            });
        }

        private static void ConfigureShaderStripping()
        {
            UnityEngine.Object[] graphicsAssets = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/GraphicsSettings.asset");
            if (graphicsAssets.Length == 0)
            {
                throw new InvalidOperationException("ProjectSettings/GraphicsSettings.asset could not be loaded.");
            }

            var graphics = new SerializedObject(graphicsAssets[0]);

            // 1 = Custom in the Graphics settings UI.
            SetInt(graphics, "m_LightmapStripping", 1);
            SetInt(graphics, "m_FogStripping", 1);

            string[] keepFlags =
            {
                "m_LightmapKeepPlain", "m_LightmapKeepDirCombined", "m_LightmapKeepDynamicPlain",
                "m_LightmapKeepDynamicDirCombined", "m_LightmapKeepShadowMask", "m_LightmapKeepSubtractive",
                "m_FogKeepLinear", "m_FogKeepExp", "m_FogKeepExp2",
            };

            foreach (string flag in keepFlags)
            {
                SetBool(graphics, flag, true);
            }

            graphics.ApplyModifiedPropertiesWithoutUndo();
        }

        [MenuItem("ONWARD Phase0/3. Build Phase0_Room scene")]
        public static void BuildRoom()
        {
            RunGuarded(() =>
            {
                EnsureFolder(WorkRoot);
                EnsureFolder(WorkRoot + "/Meshes");
                EnsureFolder(WorkRoot + "/Materials");

                Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

                Mesh cube = GetOrCreateCubeMesh();
                Material floorMaterial = GetOrCreateMaterial(FloorMaterialPath, new Color(0.55f, 0.55f, 0.52f));
                Material wallMaterial = GetOrCreateMaterial(WallMaterialPath, new Color(0.78f, 0.74f, 0.66f));

                var visualRoot = new GameObject("Visual");
                var collisionRoot = new GameObject("Collision");

                // Floor: top surface at y = 0.
                AddBlock("Floor", new Vector3(0f, -FloorThickness / 2f, 0f),
                    new Vector3(InteriorSize + 2f * WallThickness, FloorThickness, InteriorSize + 2f * WallThickness),
                    cube, floorMaterial, visualRoot.transform, collisionRoot.transform);

                float half = InteriorSize / 2f + WallThickness / 2f;
                float wallY = WallHeight / 2f;
                float fullLength = InteriorSize + 2f * WallThickness;

                AddBlock("Wall_North", new Vector3(0f, wallY, half), new Vector3(fullLength, WallHeight, WallThickness),
                    cube, wallMaterial, visualRoot.transform, collisionRoot.transform);
                AddBlock("Wall_East", new Vector3(half, wallY, 0f), new Vector3(WallThickness, WallHeight, InteriorSize),
                    cube, wallMaterial, visualRoot.transform, collisionRoot.transform);
                AddBlock("Wall_West", new Vector3(-half, wallY, 0f), new Vector3(WallThickness, WallHeight, InteriorSize),
                    cube, wallMaterial, visualRoot.transform, collisionRoot.transform);

                // South wall with a doorway centred on x = 0. The doorway has no collider.
                float segmentLength = (fullLength - DoorWidth) / 2f;
                float segmentCenterX = DoorWidth / 2f + segmentLength / 2f;
                AddBlock("Wall_South_Left", new Vector3(-segmentCenterX, wallY, -half), new Vector3(segmentLength, WallHeight, WallThickness),
                    cube, wallMaterial, visualRoot.transform, collisionRoot.transform);
                AddBlock("Wall_South_Right", new Vector3(segmentCenterX, wallY, -half), new Vector3(segmentLength, WallHeight, WallThickness),
                    cube, wallMaterial, visualRoot.transform, collisionRoot.transform);

                float lintelHeight = WallHeight - DoorHeight;
                AddBlock("Wall_South_Lintel", new Vector3(0f, DoorHeight + lintelHeight / 2f, -half), new Vector3(DoorWidth, lintelHeight, WallThickness),
                    cube, wallMaterial, visualRoot.transform, collisionRoot.transform);

                // Outdoor apron so players who leave through the door stand on ground.
                AddBlock("Apron_South", new Vector3(0f, -FloorThickness / 2f, -half - 3f), new Vector3(6f, FloorThickness, 6f - WallThickness),
                    cube, floorMaterial, visualRoot.transform, collisionRoot.transform);

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

                GameObject tent = InstantiatePrefab("Tent.prefab");
                tent.transform.position = new Vector3(0f, -30f, 0f);

                GameObject mapQuad = InstantiatePrefab("Map Quad.prefab");
                mapQuad.transform.position = new Vector3(0f, 0f, -1.5f);
                mapQuad.transform.rotation = Quaternion.identity;
                mapQuad.GetComponent<MapQuad>().LevelSize = 20f;

                GameObject damage = InstantiatePrefab("DamageVolume.prefab");
                damage.transform.position = new Vector3(0f, -80f, 0f);
                damage.transform.localScale = new Vector3(300f, 10f, 300f);

                SpawnSystem attackers = CreateSpawnSystem("SpawnsAttacking", 4.2f);
                SpawnSystem defenders = CreateSpawnSystem("SpawnsDefending", -2.4f);

                GameObject objective = InstantiatePrefab("Objectives/Obj_Uplink.prefab");
                objective.transform.position = new Vector3(0f, 0f, 1f);
                var uplink = objective.GetComponent<ObjectiveUplink>();
                uplink.AttackingTeamSpawns = new[] { attackers };
                uplink.DefendingTeamSpawns = new[] { defenders };

                GameObject freeRoam = InstantiatePrefab("Game Modes/GM_FreeRoam.prefab");
                var freeRoamMode = freeRoam.GetComponent<GM_FreeRoam>();
                freeRoamMode.AttackingTeamSpawns = new[] { attackers };
                freeRoamMode.DefendingTeamSpawns = new[] { defenders };

                GameObject uplinkModeObject = InstantiatePrefab("Game Modes/GM_Uplink.prefab");
                uplinkModeObject.GetComponent<GM_Uplink>().Objectives = new[] { uplink };

                EditorSceneManager.SaveScene(scene, ScenePath);

                SharedLevelSettings shared = GetOrCreateSharedSettings();
                shared.Scene_PC = null;
                shared.Scene_Quest = AssetDatabase.LoadAssetAtPath<SceneAsset>(ScenePath);
                shared.UseBuiltInSkybox = true;
                EditorUtility.SetDirty(shared);

                GameObject descriptorObject = InstantiatePrefab("LevelDescriptor.prefab");
                var descriptor = descriptorObject.GetComponent<LevelDescriptor>();
                descriptor.SharedSettings = shared;
                descriptor.Title = "Phase0 Room";
                descriptor.Description = "Phase 0 compatibility probe: floor, four walls, doorway, colliders, spawns.";
                EditorUtility.SetDirty(descriptor);

                BakeLighting();

                EditorSceneManager.MarkSceneDirty(scene);
                EditorSceneManager.SaveScene(scene, ScenePath);
                AssetDatabase.SaveAssets();

                Debug.Log($"{LogPrefix} Scene saved: {ScenePath}. Lightmaps: {LightmapSettings.lightmaps.Length}");
            });
        }

        [MenuItem("ONWARD Phase0/4. Build for Quest via Onward Launcher")]
        public static void BuildQuestViaLauncher()
        {
            RunGuarded(() =>
            {
                string outDir = GetCommandLineValue("-phase0Out")
                    ?? Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Phase0Out"));
                Directory.CreateDirectory(outDir);

                EditorPrefs.SetString(LauncherBinPathPref, outDir);
                EditorPrefs.SetInt(LauncherPlatformPref, LauncherPlatformQuest);
                Debug.Log($"{LogPrefix} Launcher output folder: {outDir}");

                EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);

                MethodInfo build = typeof(OnwardLauncher).GetMethod("BuildForPlatform", BindingFlags.Instance | BindingFlags.NonPublic);
                if (build == null)
                {
                    throw new MissingMethodException("OnwardLauncher.BuildForPlatform not found. The SDK version may differ from v1.15.");
                }

                var launcher = ScriptableObject.CreateInstance<OnwardLauncher>();
                try
                {
                    build.Invoke(launcher, new object[] { BuildTarget.Android, "Phase0 Quest build finished." });
                }
                catch (TargetInvocationException e)
                {
                    ReportBundle();
                    throw e.InnerException ?? e;
                }
                finally
                {
                    UnityEngine.Object.DestroyImmediate(launcher);
                }

                ReportBundle();

                string content = Path.Combine(outDir, $"unity-export.local.{CustomContentUtils.CONTENT_EXTENSION}");
                string info = Path.Combine(outDir, $"unity-export.local.{CustomContentUtils.INFO_EXTENSION}");
                if (!File.Exists(content) || !File.Exists(info))
                {
                    throw new FileNotFoundException($"Launcher did not produce both files in {outDir}.");
                }

                Debug.Log($"{LogPrefix} Launcher output OK: {content} ({new FileInfo(content).Length} bytes), {info}");
            });
        }

        private static void ReportBundle()
        {
            string bundle = Path.GetFullPath(Path.Combine(Application.dataPath, "..", ContentBuilder.FullAssetBundlePathForBuildTarget(BuildTarget.Android)));
            Debug.Log(File.Exists(bundle)
                ? $"{LogPrefix} Asset bundle present: {bundle} ({new FileInfo(bundle).Length} bytes)"
                : $"{LogPrefix} Asset bundle missing: {bundle}");
        }

        private static void BakeLighting()
        {
            var settings = AssetDatabase.LoadAssetAtPath<LightingSettings>(LightingSettingsPath);
            if (settings == null)
            {
                settings = new LightingSettings { name = "Phase0_Room" };
                AssetDatabase.CreateAsset(settings, LightingSettingsPath);
            }

            settings.bakedGI = true;
            settings.realtimeGI = false;
            settings.mixedBakeMode = MixedLightingMode.Subtractive;
            settings.lightmapper = LightingSettings.Lightmapper.ProgressiveCPU;
            settings.lightmapResolution = 8f;
            settings.lightmapPadding = 2;
            settings.lightmapMaxSize = 1024;
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

        private static SpawnSystem CreateSpawnSystem(string name, float rowZ)
        {
            GameObject go = InstantiatePrefab("SpawnSystem.prefab");
            go.name = name;
            go.transform.position = new Vector3(0f, 0.05f, rowZ);

            var system = go.GetComponent<SpawnSystem>();
            Spawnpoint[] points = system.AllSpawns;
            if (points.Length < 5)
            {
                throw new InvalidOperationException($"{name}: SDK SpawnSystem prefab has {points.Length} spawn points; at least 5 are required.");
            }

            const int columns = 4;
            const float spacing = 1.2f;
            for (int i = 0; i < points.Length; i++)
            {
                int column = i % columns;
                int row = i / columns;
                points[i].transform.localPosition = new Vector3((column - (columns - 1) / 2f) * spacing, 0f, -row * spacing);
                points[i].transform.localRotation = Quaternion.identity;
            }

            return system;
        }

        private static void AddBlock(string name, Vector3 center, Vector3 size, Mesh mesh, Material material, Transform visualParent, Transform collisionParent)
        {
            var visual = new GameObject(name);
            visual.transform.SetParent(visualParent, false);
            visual.transform.localPosition = center;
            visual.transform.localScale = size;
            visual.AddComponent<MeshFilter>().sharedMesh = mesh;
            visual.AddComponent<MeshRenderer>().sharedMaterial = material;
            GameObjectUtility.SetStaticEditorFlags(visual,
                StaticEditorFlags.ContributeGI | StaticEditorFlags.BatchingStatic |
                StaticEditorFlags.OccluderStatic | StaticEditorFlags.OccludeeStatic);

            var collision = new GameObject(name + "_Col");
            collision.transform.SetParent(collisionParent, false);
            collision.transform.localPosition = center;
            collision.AddComponent<BoxCollider>().size = size;
            GameObjectUtility.SetStaticEditorFlags(collision, StaticEditorFlags.BatchingStatic);
        }

        private static Mesh GetOrCreateCubeMesh()
        {
            var existing = AssetDatabase.LoadAssetAtPath<Mesh>(CubeMeshPath);
            if (existing != null)
            {
                return existing;
            }

            GameObject temp = GameObject.CreatePrimitive(PrimitiveType.Cube);
            Mesh mesh = UnityEngine.Object.Instantiate(temp.GetComponent<MeshFilter>().sharedMesh);
            UnityEngine.Object.DestroyImmediate(temp);

            mesh.name = "Phase0_Cube_UV2";
            Unwrapping.GenerateSecondaryUVSet(mesh);
            AssetDatabase.CreateAsset(mesh, CubeMeshPath);
            return mesh;
        }

        private static Material GetOrCreateMaterial(string path, Color color)
        {
            var existing = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (existing != null)
            {
                return existing;
            }

            var shader = AssetDatabase.LoadAssetAtPath<Shader>(LitShaderPath);
            if (shader == null)
            {
                throw new InvalidOperationException($"Missing SDK shader at {LitShaderPath}.");
            }

            var material = new Material(shader) { name = Path.GetFileNameWithoutExtension(path) };
            if (material.HasProperty("_BaseColor"))
            {
                material.SetColor("_BaseColor", color);
            }
            else if (material.HasProperty("_Color"))
            {
                material.SetColor("_Color", color);
            }
            else
            {
                Debug.LogWarning($"{LogPrefix} {shader.name} exposes no colour property; material keeps its default.");
            }

            AssetDatabase.CreateAsset(material, path);
            return material;
        }

        private static SharedLevelSettings GetOrCreateSharedSettings()
        {
            var existing = AssetDatabase.LoadAssetAtPath<SharedLevelSettings>(SharedSettingsPath);
            if (existing != null)
            {
                return existing;
            }

            var shared = ScriptableObject.CreateInstance<SharedLevelSettings>();
            AssetDatabase.CreateAsset(shared, SharedSettingsPath);
            return shared;
        }

        private static GameObject InstantiatePrefab(string relativePath)
        {
            string path = $"{PrefabRoot}/{relativePath}";
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            if (prefab == null)
            {
                throw new InvalidOperationException($"Missing SDK prefab: {path}");
            }

            return (GameObject)PrefabUtility.InstantiatePrefab(prefab);
        }

        private static void EnsureFolder(string assetPath)
        {
            if (AssetDatabase.IsValidFolder(assetPath))
            {
                return;
            }

            string parent = Path.GetDirectoryName(assetPath)?.Replace('\\', '/');
            string leaf = Path.GetFileName(assetPath);
            if (!string.IsNullOrEmpty(parent) && !AssetDatabase.IsValidFolder(parent))
            {
                EnsureFolder(parent);
            }

            AssetDatabase.CreateFolder(parent, leaf);
        }

        private static void SetInt(SerializedObject so, string property, int value)
        {
            SerializedProperty p = so.FindProperty(property)
                ?? throw new InvalidOperationException($"GraphicsSettings has no property {property}.");
            p.intValue = value;
        }

        private static void SetBool(SerializedObject so, string property, bool value)
        {
            SerializedProperty p = so.FindProperty(property)
                ?? throw new InvalidOperationException($"GraphicsSettings has no property {property}.");
            p.boolValue = value;
        }

        private static string GetCommandLineValue(string flag)
        {
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == flag)
                {
                    return args[i + 1];
                }
            }

            return null;
        }

        private static void RunGuarded(Action action)
        {
            try
            {
                action();
                Debug.Log($"{LogPrefix} OK");
                ExitIfBatch(0);
            }
            catch (Exception e)
            {
                Debug.LogError($"{LogPrefix} FAILED: {e.GetType().Name}: {e.Message}\n{e.StackTrace}");
                ExitIfBatch(1);
                if (!Application.isBatchMode)
                {
                    throw;
                }
            }
        }

        private static void ExitIfBatch(int code)
        {
            if (Application.isBatchMode)
            {
                EditorApplication.Exit(code);
            }
        }
    }
}
#endif
